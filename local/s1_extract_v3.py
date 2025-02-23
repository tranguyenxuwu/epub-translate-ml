import os
import logging
from typing import List, Optional, Tuple, Dict
from dataclasses import dataclass
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, Tag
from PIL import Image
from io import BytesIO
import base64
import urllib.parse

@dataclass
class ExtractorConfig:
    """Configuration settings for the EPUB extractor"""
    min_image_width: int = 128  # Changed to match reference code
    min_image_height: int = 128  # Changed to match reference code
    image_format: str = 'JPEG'
    output_encoding: str = 'utf-8'
    parser: str = 'html.parser'  # Changed to match reference code

class EPUBProcessor:
    """A class to process EPUB files including metadata, content, and images"""
    
    def __init__(self, epub_path: str, image_folder: str, config: Optional[ExtractorConfig] = None):
        """Initialize the EPUB processor"""
        self.epub_path = epub_path
        self.image_folder = image_folder
        self.config = config or ExtractorConfig()
        self.book = None
        self.content = []
        self.image_log = []
        
        # Create image folder if it doesn't exist
        os.makedirs(image_folder, exist_ok=True)
        
        # Setup logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)

    def extract_metadata(self) -> Dict[str, str]:
        """Extract metadata from the EPUB file"""
        metadata = {}
        
        def get_meta(name: str) -> str:
            meta = self.book.get_metadata('DC', name)
            return meta[0][0].strip() if meta else "Không có"
        
        metadata["Title"] = get_meta('title')
        metadata["Author"] = get_meta('creator')
        metadata["Publisher"] = get_meta('publisher')
        metadata["Date"] = get_meta('date')
        metadata["Language"] = get_meta('language')
        
        return metadata

    def process_ruby_text(self, tag: Tag) -> str:
        """Process ruby tags and return formatted text"""
        if tag.name == 'rb':
            rt_tag = tag.find_next_sibling('rt')
            if rt_tag:
                return f"({tag.get_text().strip()})"
        return tag.get_text().strip()

    def process_image(self, img_tag: Tag, img_data: bytes) -> Optional[str]:
        """Process and save an image, return path if successful"""
        try:
            image = Image.open(BytesIO(img_data))
            if image.width >= self.config.min_image_width and image.height >= self.config.min_image_height:
                image_name = f"image-{img_tag.sourceline}-{image.width}x{image.height}.jpg"
                image_path = os.path.join(self.image_folder, image_name)
                image.save(image_path, self.config.image_format)
                self.image_log.append(
                    f"Đã phát hiện {image_name} tại dòng {img_tag.sourceline}. "
                    f"Kích thước: {image.width}x{image.height}"
                )
                return image_path
            return None
        except Exception as e:
            self.logger.error(f"Lỗi khi xử lý hình ảnh: {str(e)}")
            return None

    def extract_image_from_item(self, image_href: str) -> Optional[bytes]:
        """Extract image data from EPUB item"""
        try:
            # Xử lý URL encoding trong href
            decoded_href = urllib.parse.unquote(image_href)
            
            # Thử tìm item trực tiếp
            img_item = self.book.get_item_with_href(decoded_href)
            
            # Nếu không tìm thấy, thử tìm với các biến thể của đường dẫn
            if not img_item:
                clean_href = decoded_href.lstrip('./').lstrip('../')
                img_item = self.book.get_item_with_href(clean_href)
            
            if img_item:
                return img_item.get_content()
            
            # Xử lý trường hợp base64
            if image_href.startswith('data:image'):
                header, encoded = image_href.split(',', 1)
                return base64.b64decode(encoded)
                
            return None
        except Exception as e:
            self.logger.error(f"Lỗi khi trích xuất hình ảnh {image_href}: {str(e)}")
            return None

    def process_paragraph(self, tag: Tag) -> Optional[str]:
        """Process a paragraph tag and return its content or None if empty"""
        paragraph_content = []
        for element in tag.contents:
            if element.name == 'ruby':
                rb_tag = element.find('rb')
                if rb_tag:
                    paragraph_content.append(self.process_ruby_text(rb_tag))
            elif element.name == 'rb':
                paragraph_content.append(self.process_ruby_text(element))
            else:
                if hasattr(element, 'get_text'):
                    paragraph_content.append(element.get_text().strip())
                else:
                    paragraph_content.append(str(element).strip())
        
        text = ''.join(paragraph_content).strip()
        if text:
            return f"P: {text}"
        return "\n"

    def process_content(self):
        """Process EPUB content"""
        items = list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
        
        for item in items:
            soup = BeautifulSoup(item.get_body_content(), self.config.parser)
            
            # Process title
            title_tag = soup.find('title')
            if title_tag:
                self.content.append(f"TITLE: {title_tag.get_text().strip()}")
            
            # Process h1 tags
            for h1_tag in soup.find_all('h1'):
                ruby_text = ''.join(self.process_ruby_text(tag) 
                                  for tag in h1_tag.find_all(['rb', 'text']))
                if ruby_text:
                    self.content.append(f"H1: {ruby_text}")
                else:
                    self.content.append(f"H1: {h1_tag.get_text().strip()}")
            
            # Process paragraphs
            for p_tag in soup.find_all('p'):
                processed_p = self.process_paragraph(p_tag)
                if processed_p:
                    self.content.append(processed_p)
            
            # Process images
            for img_tag in soup.find_all('img'):
                img_src = img_tag.get('src')
                if img_src:
                    img_data = self.extract_image_from_item(img_src)
                    if img_data:
                        image_path = self.process_image(img_tag, img_data)
                        if image_path:
                            self.content.append(f"IMAGE: {image_path}")

    def process(self):
        """Main processing method"""
        try:
            # Read EPUB file
            self.book = epub.read_epub(self.epub_path)
            
            # Extract metadata
            metadata = self.extract_metadata()
            
            # Process content
            self.content = ["===== METADATA ====="]
            self.content.extend(f"{k}: {v}" for k, v in metadata.items())
            self.content.append("\n===== NỘI DUNG =====")
            
            # Process all content
            self.process_content()
            
            # Remove consecutive newlines
            processed_content = []
            prev_was_newline = False
            for line in self.content:
                if line == "\n":
                    if not prev_was_newline:
                        processed_content.append(line)
                        prev_was_newline = True
                else:
                    processed_content.append(line)
                    prev_was_newline = False
            
            # Save content
            with open('output.txt', 'w', encoding=self.config.output_encoding) as output_file:
                output_file.write('\n'.join(processed_content))
            
            # Save image log if there are any images
            if self.image_log:
                with open('image_log.txt', 'w', encoding=self.config.output_encoding) as log_file:
                    log_file.write('\n'.join(self.image_log))
            
            self.logger.info("✅ Hoàn tất trích xuất EPUB!")
            return True
            
        except Exception as e:
            self.logger.error(f"Error processing EPUB: {str(e)}")
            return False

def main():
    """Main entry point"""
    epub_path = './book2.epub'
    image_folder = 'extracted_images'
    
    processor = EPUBProcessor(epub_path, image_folder)
    processor.process()

if __name__ == "__main__":
    main()