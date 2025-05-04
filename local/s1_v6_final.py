import os
import logging
from typing import List, Optional, Dict, Set, Tuple
from dataclasses import dataclass
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, Tag
from PIL import Image
from io import BytesIO
import base64
import urllib.parse
import hashlib
from pathlib import Path

@dataclass
class ExtractorConfig:
    """Configuration settings for the EPUB processor"""
    min_image_width: int = 128
    min_image_height: int = 128
    image_format: str = 'JPEG'
    quality: int = 95
    output_encoding: str = 'utf-8'
    parser: str = 'html.parser'
    output_dir: str = 'output'
    image_dir: str = 'images'

class ImageProcessor:
    """Handles all image-related operations including SVG"""
    
    def __init__(self, image_dir: Path, logger: logging.Logger):
        self.image_dir = image_dir
        self.logger = logger
        self.processed_images: Set[str] = set()
        self.image_log: List[Dict] = []

    def get_dimensions(self, tag: Tag) -> Tuple[Optional[int], Optional[int]]:
        """Extract image dimensions from tag attributes including SVG"""
        try:
            width = (tag.get('width') or
                    tag.get('data-width') or
                    tag.get('{http://www.w3.org/1999/xlink}width'))
            height = (tag.get('height') or
                     tag.get('data-height') or
                     tag.get('{http://www.w3.org/1999/xlink}height'))
            
            width_val = None
            height_val = None
            if width:
                width_val = int(float(str(width).replace('px', '').strip()))
            if height:
                height_val = int(float(str(height).replace('px', '').strip()))
                
            return width_val, height_val
        except (ValueError, TypeError):
            return None, None

    def get_source(self, tag: Tag) -> Optional[str]:
        """Extract image source from various attribute formats including SVG"""
        return (tag.get('src') or
                tag.get('xlink:href') or
                tag.get('{http://www.w3.org/1999/xlink}href') or
                tag.get('href') or
                tag.get('data-src'))

    def compute_hash(self, data: bytes) -> str:
        """Generate unique hash for image data"""
        return hashlib.md5(data).hexdigest()

    def save(self, image_data: bytes, source_info: str = "",
             width: Optional[int] = None, height: Optional[int] = None,
             config: ExtractorConfig = None) -> Optional[str]:
        """Save image with deduplication"""
        try:
            image_hash = self.compute_hash(image_data)
            if image_hash in self.processed_images:
                return None

            with Image.open(BytesIO(image_data)) as img:
                actual_width = width or img.width
                actual_height = height or img.height
                
                if (actual_width < config.min_image_width or 
                    actual_height < config.min_image_height):
                    return None

                filename = f"{image_hash}_{actual_width}x{actual_height}.jpg"
                filepath = self.image_dir / filename

                if img.mode in ('RGBA', 'LA'):
                    background = Image.new('RGB', img.size, 'white')
                    background.paste(img, mask=img.split()[-1])
                    background.save(filepath, config.image_format, 
                                 quality=config.quality)
                else:
                    img.convert('RGB').save(filepath, config.image_format,
                                         quality=config.quality)

                self.image_log.append({
                    'filename': filename,
                    'dimensions': f"{actual_width}x{actual_height}",
                    'source': source_info,
                    'hash': image_hash
                })
                
                self.processed_images.add(image_hash)
                return str(filepath)

        except Exception as e:
            self.logger.error(f"Error saving image: {str(e)}")
            return None

class ContentProcessor:
    """Handles content extraction and processing including SVG"""
    
    def __init__(self, book: epub.EpubBook, image_processor: ImageProcessor, 
                 logger: logging.Logger):
        self.book = book
        self.image_processor = image_processor
        self.logger = logger
        self.content: List[str] = []

    def extract_image_data(self, href: str) -> Optional[bytes]:
        """Extract image data from EPUB by href"""
        try:
            if href.startswith('data:image'):
                header, encoded = href.split(',', 1)
                return base64.b64decode(encoded)

            decoded_href = urllib.parse.unquote(href)
            clean_href = decoded_href.lstrip('./').lstrip('../')
            
            for test_href in [clean_href, decoded_href, href]:
                if item := self.book.get_item_with_href(test_href):
                    return item.get_content()

            return None
        except Exception as e:
            self.logger.error(f"Error extracting image {href}: {str(e)}")
            return None

    def process_image(self, tag: Tag, context: str, 
                     config: ExtractorConfig) -> Optional[str]:
        """Process image tag including SVG <image> elements"""
        if tag.name in ['img', 'image']:
            if src := self.image_processor.get_source(tag):
                if image_data := self.extract_image_data(src):
                    width, height = self.image_processor.get_dimensions(tag)
                    source_info = f"Context: {context}, Source: {src}"
                    return self.image_processor.save(image_data, source_info,
                                                  width, height, config)
        return None

    def process_ruby(self, tag: Tag) -> str:
        """Process ruby text formatting"""
        if tag.name == 'ruby':
            base = tag.find('rb')
            phonetic = tag.find('rt')
            if base and phonetic:
                return f"{base.get_text().strip()}({phonetic.get_text().strip()})"
        return tag.get_text().strip()

    def process_heading(self, tag: Tag, config: ExtractorConfig) -> List[str]:
        """Process heading tag and contents including SVG"""
        content_lines = []
        
        for img in tag.find_all(['img', 'image']):
            if path := self.process_image(img, f"Heading {tag.name}", config):
                content_lines.append(f"IMAGE: {path}")
        
        for svg in tag.find_all('svg'):
            for img in svg.find_all('image'):
                if path := self.process_image(img, f"Heading {tag.name} SVG", config):
                    content_lines.append(f"IMAGE: {path}")
        
        text = ' '.join(
            self.process_ruby(elem) if isinstance(elem, Tag) 
            else str(elem).strip()
            for elem in tag.contents
            if not (isinstance(elem, Tag) and elem.name in ['img', 'image', 'svg'])
        ).strip()
        
        if text:
            content_lines.append(f"{tag.name.upper()}: {text}")
        
        return content_lines

    def process_paragraph(self, tag: Tag, config: ExtractorConfig) -> Optional[str]:
        """Process paragraph tag and contents including SVG"""
        parts = []
        
        for elem in tag.contents:
            if isinstance(elem, Tag):
                if elem.name in ['img', 'image']:
                    if path := self.process_image(elem, "Paragraph", config):
                        parts.append(f"[Image: {path}]")
                elif elem.name == 'svg':
                    for img in elem.find_all('image'):
                        if path := self.process_image(img, "Paragraph SVG", config):
                            parts.append(f"[Image: {path}]")
                else:
                    parts.append(self.process_ruby(elem))
            else:
                parts.append(str(elem).strip())
        
        text = ' '.join(parts).strip()
        return f"P: {text}" if text else None

class EPUBProcessor:
    """Main EPUB processing class with SVG support"""
    
    def __init__(self, epub_path: str, config: Optional[ExtractorConfig] = None):
        self.epub_path = Path(epub_path)
        self.config = config or ExtractorConfig()
        
        self.base_dir = Path(self.config.output_dir)
        self.image_dir = self.base_dir / self.config.image_dir
        self.base_dir.mkdir(exist_ok=True)
        self.image_dir.mkdir(exist_ok=True)
        
        self.setup_logging()
        
        self.image_processor = ImageProcessor(self.image_dir, self.logger)
        self.book = None
        self.content_processor = None
        self.chapters: List[Tuple[str, str]] = []

    def setup_logging(self):
        """Configure logging"""
        log_format = '%(asctime)s - %(levelname)s - %(message)s'
        logging.basicConfig(
            level=logging.INFO,
            format=log_format,
            handlers=[
                logging.FileHandler(self.base_dir / 'processing.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def extract_metadata(self) -> Dict[str, str]:
        """Extract EPUB metadata"""
        def get_meta(name: str) -> str:
            meta = self.book.get_metadata('DC', name)
            return meta[0][0] if meta else "Not available"
        
        fields = ['title', 'creator', 'publisher', 'date', 'language', 'identifier']
        return {field.capitalize(): get_meta(field) for field in fields}

    def extract_chapters(self):
        """Extract chapter list from navigation documents (both navigation-documents.xhtml and nav.xhtml)"""
        nav_hrefs = ["navigation-documents.xhtml", "nav.xhtml"]  # Các href cần thử
        nav_item = None
        
        # Thử lần lượt các href
        for href in nav_hrefs:
            nav_item = self.book.get_item_with_href(href)
            if nav_item:
                break
        
        if not nav_item:
            self.logger.warning("No navigation document found")
            return
        
        try:
            soup = BeautifulSoup(nav_item.get_content(), self.config.parser)
            nav_toc = soup.find('nav', {'epub:type': 'toc'})
            if not nav_toc:
                return

            # Xử lý các thẻ <a> trong nav_toc
            for link in nav_toc.find_all('a', href=True):
                href = urllib.parse.unquote(link['href']).split('#')[0]
                normalized_href = href.lstrip('./').lstrip('../')
                self.chapters.append((normalized_href, link.get_text().strip()))
                
        except Exception as e:
            self.logger.warning(f"Error extracting chapters: {str(e)}")

    def process_content(self):
        """Process all EPUB content including SVG images"""
        for item in self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            try:
                current_href = urllib.parse.unquote(item.get_name()).split('#')[0]
                current_href = current_href.lstrip('./')
                
                chapter_title = next((title for href, title in self.chapters 
                                    if href == current_href), None)
                if chapter_title:
                    self.content_processor.content.append(f"\nCHAPTER: {chapter_title}")
                
                soup = BeautifulSoup(item.get_content(), self.config.parser)
                
                if title := soup.find('title'):
                    self.content_processor.content.append(
                        f"TITLE: {title.get_text().strip()}")
                
                for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                    self.content_processor.content.extend(
                        self.content_processor.process_heading(tag, self.config))
                
                for tag in soup.find_all('p'):
                    if content := self.content_processor.process_paragraph(
                        tag, self.config):
                        self.content_processor.content.append(content)
                
                # Process standalone images
                for tag in soup.find_all(['img', 'image']):
                    if path := self.content_processor.process_image(
                        tag, "Standalone", self.config):
                        self.content_processor.content.append(f"IMAGE: {path}")
                
                # Process SVG-contained images
                for svg in soup.find_all('svg'):
                    for img in svg.find_all('image'):
                        if path := self.content_processor.process_image(
                            img, "SVG Container", self.config):
                            self.content_processor.content.append(f"IMAGE: {path}")
                        
            except Exception as e:
                self.logger.error(f"Error processing document: {str(e)}")

    def save_results(self):
        """Save all processing results"""
        try:
            content_file = self.base_dir / 'content.txt'
            with open(content_file, 'w', encoding=self.config.output_encoding) as f:
                f.write('\n'.join(self.content_processor.content))

            if self.image_processor.image_log:
                log_file = self.base_dir / 'image_log.txt'
                with open(log_file, 'w', 
                         encoding=self.config.output_encoding) as f:
                    for entry in self.image_processor.image_log:
                        f.write(f"File: {entry['filename']}\n")
                        f.write(f"Dimensions: {entry['dimensions']}\n")
                        f.write(f"Source: {entry['source']}\n")
                        f.write(f"Hash: {entry['hash']}\n")
                        f.write("-" * 50 + "\n")

        except Exception as e:
            self.logger.error(f"Error saving results: {str(e)}")

    def process(self) -> bool:
        """Main processing method"""
        try:
            self.logger.info(f"Processing EPUB: {self.epub_path}")
            
            self.book = epub.read_epub(str(self.epub_path))
            self.content_processor = ContentProcessor(
                self.book, self.image_processor, self.logger)
            
            # Extract metadata and chapters
            self.extract_chapters()
            metadata = self.extract_metadata()
            
            # Initialize content with metadata
            self.content_processor.content = ["===== METADATA ====="]
            self.content_processor.content.extend(
                f"{k}: {v}" for k, v in metadata.items())
            self.content_processor.content.append("\n===== CONTENT =====")
            
            self.process_content()
            self.save_results()
            
            self.logger.info("EPUB processing completed successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Error processing EPUB: {str(e)}")
            return False

def main():
    """Example usage"""
    epub_path = "./book4.epub"
    config = ExtractorConfig(
        output_dir="output_v4",
        image_dir="images",
        min_image_width=500,
        min_image_height=500,
        quality=95
    )
    
    processor = EPUBProcessor(epub_path, config)
    success = processor.process()
    print("Success!" if success else "Failed - check logs")

if __name__ == "__main__":
    main()