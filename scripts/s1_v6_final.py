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
        """Extract chapter list using standard EPUB navigation properties."""
        nav_item = None
        is_ncx = False

        # 1. Try finding the EPUB 3 Nav Document (identified by properties="nav")
        try:
            nav_items_epub3 = self.book.get_items_by_properties('nav')
            nav_items_list = list(nav_items_epub3) # Convert generator to list
            if nav_items_list:
                nav_item = nav_items_list[0] # Take the first one if multiple exist
                self.logger.info(f"Found EPUB 3 navigation document: {nav_item.get_name()}")
            else:
                 self.logger.info("No EPUB 3 navigation item found (properties='nav').")
        except Exception as e:
            self.logger.warning(f"Error checking for EPUB 3 navigation document: {e}")

        # 2. If no EPUB 3 Nav, try finding the EPUB 2 NCX toc
        if not nav_item:
            self.logger.info("Attempting to find EPUB 2 NCX navigation.")
            try:
                # Use ITEM_NAVIGATION type to find NCX
                nav_items_ncx = self.book.get_items_of_type(ebooklib.ITEM_NAVIGATION)
                nav_items_list = list(nav_items_ncx) # Convert generator to list
                if nav_items_list:
                    nav_item = nav_items_list[0]
                    is_ncx = True
                    self.logger.info(f"Found EPUB 2 NCX navigation document: {nav_item.get_name()}")
                else:
                    self.logger.info("No EPUB 2 NCX navigation item found.")
            except Exception as e:
                self.logger.warning(f"Error checking for EPUB 2 NCX navigation document: {e}")

        # 3. Fallback: Try the original filename guessing (less reliable)
        if not nav_item:
            self.logger.warning("No standard navigation item found. Falling back to filename guessing.")
            nav_hrefs_to_try = [
                "nav.xhtml", # Common EPUB 3 name
                "navigation-documents.xhtml", # The one you mentioned
                "toc.ncx", # Common EPUB 2 name
                # Add variants with common paths
                "OEBPS/nav.xhtml",
                "OEBPS/navigation-documents.xhtml",
                "OEBPS/toc.ncx",
                "OPS/nav.xhtml",
                "OPS/navigation-documents.xhtml",
                "OPS/toc.ncx",
            ]
            for href in nav_hrefs_to_try:
                # Use get_item_with_href which handles path normalization better
                nav_item = self.book.get_item_with_href(href)
                if nav_item:
                    self.logger.info(f"Found navigation document by guessing filename: {href}")
                    # Check if it's likely NCX by extension or type
                    if href.lower().endswith('.ncx') or nav_item.get_type() == ebooklib.ITEM_NAVIGATION:
                        is_ncx = True
                    break # Stop after finding one

        # 4. Process the found navigation item
        if not nav_item:
            self.logger.error("FATAL: Could not find any navigation document (EPUB 3, NCX, or guessed filenames). Chapter extraction failed.")
            return # Exit if no nav doc found

        try:
            self.logger.info(f"Processing navigation item: {nav_item.get_name()} (Type: {'NCX' if is_ncx else 'XHTML'})")
            content = nav_item.get_content()
            if is_ncx:
                # NCX parsing uses different tags (navMap, navPoint, content src)
                soup = BeautifulSoup(content, 'xml') # Use 'xml' parser for NCX
                for nav_point in soup.find_all('navPoint'):
                    # Find the navLabel and text within it
                    nav_label = nav_point.find('navLabel')
                    label = nav_label.find('text').get_text().strip() if nav_label and nav_label.find('text') else "Untitled"

                    content_tag = nav_point.find('content')
                    if content_tag and content_tag.get('src'):
                        href = urllib.parse.unquote(content_tag['src']).split('#')[0]
                        # Normalize path relative to the NCX file if needed,
                        # but ebooklib often handles this internally when getting items later.
                        # For consistency, let's keep the simple normalization for now.
                        normalized_href = href.lstrip('./').lstrip('../')
                        self.chapters.append((normalized_href, label))
            else:
                # XHTML parsing (nav, ol, li, a href)
                soup = BeautifulSoup(content, self.config.parser)
                # Try finding nav[epub:type="toc"] first
                nav_toc = soup.find('nav', {'epub:type': 'toc'})
                # If not found, try finding any <nav> element
                if not nav_toc:
                     nav_toc = soup.find('nav')
                # If still not found, fall back to the whole body
                if not nav_toc:
                     nav_toc = soup.body

                if nav_toc:
                    # Find all 'a' tags with an 'href' attribute within the selected container
                    for link in nav_toc.find_all('a', href=True):
                        href_raw = link['href']
                        if not href_raw: # Skip empty hrefs
                            continue

                        # Decode URL encoding and remove fragment identifier
                        href = urllib.parse.unquote(href_raw).split('#')[0]

                        # Basic normalization: remove leading './' or '../'
                        # A more robust solution might use urljoin with the nav doc's base URI,
                        # but ebooklib's get_item_with_href usually handles relative paths well.
                        normalized_href = href.lstrip('./').lstrip('../')

                        # Ensure the href is not empty after stripping fragment
                        if normalized_href:
                             link_text = link.get_text().strip()
                             if not link_text: # Try getting text from nested tags if link text is empty
                                 link_text = ' '.join(t.strip() for t in link.find_all(string=True) if t.strip())
                             self.chapters.append((normalized_href, link_text or "Untitled"))
                        else:
                            self.logger.warning(f"Skipping link with fragment-only or empty href: {href_raw}")
                else:
                    self.logger.warning(f"Could not find a suitable navigation container (nav[epub:type=toc], nav, body) within document {nav_item.get_name()}")


        except Exception as e:
            # Use traceback for more detailed error logging if needed
            # import traceback
            # self.logger.error(f"Error parsing navigation document {nav_item.get_name()}: {str(e)}\n{traceback.format_exc()}")
            self.logger.error(f"Error parsing navigation document {nav_item.get_name()}: {str(e)}")


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
    epub_path = r"C:\Users\Fubuki\Downloads\gai_nga_v4_bak.epub"
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