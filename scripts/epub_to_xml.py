import os
import logging
from typing import List, Optional, Dict, Set, Tuple, Any
from dataclasses import dataclass
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, Tag # Keep Tag import
from PIL import Image
from io import BytesIO
import base64
import urllib.parse
import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET
from xml.dom import minidom # For pretty printing XML

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
    xml_filename: str = 'content.xml'

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
                try:
                    width_val = int(float(str(width).replace('px', '').strip()))
                except ValueError:
                     self.logger.warning(f"Could not parse width attribute: {width}")
            if height:
                try:
                    height_val = int(float(str(height).replace('px', '').strip()))
                except ValueError:
                    self.logger.warning(f"Could not parse height attribute: {height}")

            return width_val, height_val
        except (ValueError, TypeError) as e:
            self.logger.error(f"Error getting dimensions: {e}")
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
             config: ExtractorConfig = None) -> Optional[Dict[str, Any]]:
        """Save image with deduplication. Returns dict with path and dimensions if saved."""
        if not config:
             self.logger.error("ImageProcessor.save called without config!")
             return None
        try:
            image_hash = self.compute_hash(image_data)

            existing_log = next((log for log in self.image_log if log['hash'] == image_hash), None)
            if existing_log:
                 self.logger.debug(f"Image hash {image_hash} already processed. Reusing path: {existing_log['filepath']}")
                 return {
                     'filepath': existing_log['filepath'],
                     'filename': existing_log['filename'],
                     'dimensions': existing_log['dimensions'],
                     'hash': image_hash,
                     'source': source_info
                 }

            with Image.open(BytesIO(image_data)) as img:
                actual_width = width if width is not None else img.width
                actual_height = height if height is not None else img.height

                if (actual_width < config.min_image_width or
                    actual_height < config.min_image_height):
                    self.logger.info(f"Skipping image {source_info} due to dimensions {actual_width}x{actual_height} < minimum {config.min_image_width}x{config.min_image_height}")
                    return None

                img_extension = config.image_format.lower() if config.image_format != 'JPEG' else 'jpg'
                filename = f"{image_hash}_{actual_width}x{actual_height}.{img_extension}"
                filepath = self.image_dir / filename

                if filepath.exists():
                     self.logger.debug(f"Image file already exists: {filepath}. Skipping save operation.")
                     if image_hash not in self.processed_images:
                         pass
                     else:
                         return {
                            'filepath': str(filepath),
                            'filename': filename,
                            'dimensions': f"{actual_width}x{actual_height}",
                            'hash': image_hash,
                            'source': source_info
                        }
                else:
                    self.image_dir.mkdir(parents=True, exist_ok=True)
                    if img.mode in ('RGBA', 'LA', 'P'):
                        img = img.convert('RGBA')
                        background = Image.new('RGB', img.size, 'white')
                        background.paste(img, mask=img.split()[-1])
                        background.save(filepath, config.image_format, quality=config.quality)
                    elif img.mode == 'CMYK':
                         img = img.convert('RGB')
                         img.save(filepath, config.image_format, quality=config.quality)
                    else:
                        img.convert('RGB').save(filepath, config.image_format, quality=config.quality)
                    self.logger.debug(f"Saved image to {filepath}")


                image_details = {
                    'filepath': str(filepath),
                    'filename': filename,
                    'dimensions': f"{actual_width}x{actual_height}",
                    'source': source_info,
                    'hash': image_hash
                }

                if image_hash not in self.processed_images:
                    self.image_log.append(image_details)
                    self.processed_images.add(image_hash)

                return image_details

        except Exception as e:
            # Use Vietnamese in error message if desired, otherwise English is fine
            self.logger.error(f"Lỗi khi lưu hình ảnh từ source '{source_info}': {str(e)}", exc_info=True)
            return None

class ContentProcessor:
    """Handles content extraction and processing into a structured list"""

    def __init__(self, book: epub.EpubBook, image_processor: ImageProcessor,
                 logger: logging.Logger):
        self.book = book
        self.image_processor = image_processor
        self.logger = logger
        self.structured_content: List[Dict[str, Any]] = []
        self.current_chapter_href: Optional[str] = None

    def extract_image_data(self, href: str, base_href: Optional[str] = None) -> Optional[bytes]:
        """Extract image data from EPUB by href, considering relative paths"""
        try:
            if href.startswith('data:image'):
                header, encoded = href.split(',', 1)
                return base64.b64decode(encoded)

            absolute_href = href
            if base_href and not href.startswith(('http://', 'https://', '/')):
                 base_path = Path(base_href).parent
                 absolute_href = str(base_path / href).replace('\\', '/')

            decoded_href = urllib.parse.unquote(absolute_href)
            potential_hrefs = [
                decoded_href, absolute_href, href,
                decoded_href.lstrip('./').lstrip('../'),
                absolute_href.lstrip('./').lstrip('../'),
                href.lstrip('./').lstrip('../')
            ]
            common_roots = ['OEBPS', 'OPS', 'EPUB', 'Text', 'text'] # Added 'text' common in some EPUBs
            original_unquoted = urllib.parse.unquote(href)
            for root_dir in common_roots:
                 potential_hrefs.append(f"{root_dir}/{original_unquoted.lstrip('/')}")
                 potential_hrefs.append(f"{root_dir}/{href.lstrip('/')}")

            unique_hrefs = list(dict.fromkeys(potential_hrefs)) # Remove duplicates while preserving order

            for test_href in unique_hrefs:
                item = self.book.get_item_with_href(test_href)
                if item:
                    self.logger.debug(f"Found image item for href '{href}' (resolved as '{test_href}')")
                    return item.get_content()

            self.logger.warning(f"Could not find EPUB item for image href: '{href}' (Base: '{base_href}', Attempt: '{absolute_href}')")
            return None
        except Exception as e:
            self.logger.error(f"Lỗi khi trích xuất dữ liệu hình ảnh cho href '{href}': {str(e)}")
            return None

    def process_image(self, tag: Tag, context: str, config: ExtractorConfig, base_href: Optional[str]) -> Optional[Dict[str, Any]]:
        """Process image tag, save image, return structured data for XML."""
        image_info = None
        if tag.name in ['img', 'image']:
            src = self.image_processor.get_source(tag)
            if src:
                image_data = self.extract_image_data(src, base_href)
                if image_data:
                    width, height = self.image_processor.get_dimensions(tag)
                    source_info = f"Context: {context}, Source Tag: <{tag.name} src='{src}'> in doc '{base_href}'"
                    saved_image_details = self.image_processor.save(
                        image_data, source_info, width, height, config)
                    if saved_image_details:
                        image_info = {
                            "type": "image",
                            "filepath": saved_image_details['filepath'],
                            "filename": saved_image_details['filename'],
                            "alt": f"Image from {context}"
                        }
                else:
                    self.logger.warning(f"Could not extract image data for src='{src}' in context '{context}' from doc '{base_href}'")
            else:
                 self.logger.debug(f"Skipping tag <{tag.name}> in context '{context}' - no valid source attribute found.")
        return image_info

    def process_ruby(self, tag: Tag) -> str:
        """Process ruby text formatting (extracts base + phonetic)"""
        if tag.name == 'ruby':
            base = tag.find('rb')
            rt = tag.find('rt')
            rp_open = tag.find('rp', string='（') or tag.find('rp', string='(')
            rp_close = tag.find('rp', string='）') or tag.find('rp', string=')')

            base_text = base.get_text().strip() if base else ''
            rt_text = rt.get_text().strip() if rt else ''

            # If rt exists, try to reconstruct with optional rp
            if base_text and rt_text:
                open_paren = "（" if rp_open else "("
                close_paren = "）" if rp_close else ")"
                return f"{base_text}{open_paren}{rt_text}{close_paren}"
            elif base_text: # Only base exists
                return base_text
            else: # Fallback to tag text if structure is weird
                return tag.get_text().strip()
        # Fallback for non-ruby tags
        return tag.get_text().strip()


    # *** REPLACED process_tag_content ***
    def process_tag_content(self, tag: Tag, config: ExtractorConfig, context: str, base_href: str) -> List[Dict[str, Any]]:
        """Recursively process content, accumulating text across nodes and handling block/inline elements."""
        items = []
        current_text_fragments = [] # Accumulates text pieces for the current paragraph

        self.logger.debug(f"Entering process_tag_content for <{tag.name}> context: {context} in {base_href}")

        # Helper function to flush accumulated text as a paragraph
        def flush_text():
            nonlocal current_text_fragments
            if current_text_fragments:
                # Join fragments, then strip leading/trailing whitespace from the combined string
                full_text = ''.join(current_text_fragments).strip()
                # Replace multiple internal whitespaces (including newlines) with a single space
                full_text = ' '.join(full_text.split())
                if full_text:
                    self.logger.debug(f"  Flushing paragraph: '{full_text[:100]}...'")
                    items.append({"type": "paragraph", "text": full_text})
                current_text_fragments = [] # Reset fragments

        try:
            for elem in tag.contents:
                if isinstance(elem, str):
                    # Keep internal whitespace for now, handle in flush_text
                    text = elem
                    if text.strip():
                        self.logger.debug(f"  Processing text node: '{text.strip()[:50]}...'")
                    # Append raw text to fragments
                    current_text_fragments.append(text)

                elif isinstance(elem, Tag):
                    tag_name = elem.name
                    self.logger.debug(f"  Processing child tag: <{tag_name}>")

                    # --- Block-level elements that typically enforce paragraph breaks ---
                    # Added more potential block elements found in EPUBs
                    if tag_name in ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'div', 'section',
                                    'article', 'main', 'figure', 'figcaption', 'blockquote', 'pre',
                                    'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'table', 'hr', 'header', 'footer', 'aside']:
                        flush_text() # Finish any preceding paragraph

                        # Handle specific block tags if needed, otherwise recurse
                        if tag_name in ['img', 'image']: # Should ideally not be *inside* other blocks, but handle defensively
                             if image_info := self.process_image(elem, f"{context} > {tag_name}", config, base_href):
                                items.append(image_info)
                        elif tag_name == 'svg':
                            for svg_img in elem.find_all('image'):
                                 if image_info := self.process_image(svg_img, f"{context} > {tag_name} SVG", config, base_href):
                                     items.append(image_info)
                        else:
                             # Recursively process the block tag's content
                             self.logger.debug(f"  Recursing into block child <{tag_name}>")
                             nested_items = self.process_tag_content(elem, config, f"{context} > {tag_name}", base_href)
                             # Add role for headings processed recursively
                             if tag_name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                                 for nested_item in nested_items:
                                     if nested_item['type'] == 'paragraph':
                                         nested_item['role'] = tag_name # Mark paragraphs coming from headings
                             items.extend(nested_items)

                        flush_text() # Ensure break *after* the block element

                    # --- Standalone Images (often treated as block) ---
                    elif tag_name in ['img', 'image']:
                        flush_text()
                        if image_info := self.process_image(elem, context, config, base_href):
                            items.append(image_info)
                        flush_text()

                    elif tag_name == 'svg':
                        flush_text()
                        svg_images_found = False
                        for svg_img in elem.find_all('image'):
                            if image_info := self.process_image(svg_img, f"{context} SVG", config, base_href):
                                items.append(image_info)
                                svg_images_found = True
                        if not svg_images_found:
                             self.logger.debug(f"  SVG tag <{elem.name}> did not contain processable <image> tags.")
                        flush_text()

                    # --- Line breaks ---
                    elif tag_name == 'br':
                        # Append a space to fragments if needed
                        if current_text_fragments and not current_text_fragments[-1].strip().endswith(' '):
                             current_text_fragments.append(' ')
                        self.logger.debug("  Processed <br> as space.")

                    # --- Ruby characters ---
                    elif tag_name == 'ruby':
                         ruby_text = self.process_ruby(elem)
                         self.logger.debug(f"  Processing ruby tag, text: {ruby_text}")
                         current_text_fragments.append(ruby_text)

                    # --- Other tags (assumed inline or container to be recursed into) ---
                    else:
                        self.logger.debug(f"  Recursing into inline/unknown child <{tag_name}>")
                        inline_items = self.process_tag_content(elem, config, f"{context} > {tag_name}", base_href)
                        # Append text from inline items to current fragments
                        for item in inline_items:
                            if item['type'] == 'paragraph':
                                # Append text, let flush handle joining/spacing
                                current_text_fragments.append(item['text'])
                            elif item['type'] == 'image':
                                # If images appear inside inline elements, flush text first, add image, flush again
                                flush_text()
                                self.logger.warning(f"Adding image found inside suspected inline tag <{tag_name}>: {item.get('filename')}")
                                items.append(item)
                                flush_text()


        except Exception as e_tag_content:
            self.logger.error(f"Lỗi bên trong process_tag_content cho <{tag.name}> context: {context} in {base_href}: {e_tag_content}", exc_info=True)

        # Flush any remaining text at the end
        flush_text()

        self.logger.debug(f"Exiting process_tag_content for <{tag.name}>. Items found: {len(items)}")
        return items


    # *** MODIFIED process_document_item ***
    def process_document_item(self, item: ebooklib.epub.EpubHtml, config: ExtractorConfig):
        """Process a single EPUB document item and add structured content."""
        try:
            base_href = urllib.parse.unquote(item.get_name())
            self.logger.info(f"Processing document item: {base_href}")

            soup = BeautifulSoup(item.get_content(), config.parser)
            body = soup.find('body')

            if not body:
                self.logger.warning(f"No <body> tag found in document: {base_href}. Skipping content extraction for this item.")
                return

            # --- MODIFICATION START ---
            # Check for direct children first
            direct_children = body.find_all(recursive=False)
            if not direct_children:
                 # If no direct children, process the body tag itself
                 self.logger.warning(f"No direct children found under <body> in {base_href}. Processing body tag directly.")
                 body_content = self.process_tag_content(body, config, "Body Direct", base_href)
                 self.structured_content.extend(body_content)
            else:
                 # If direct children exist, process them individually
                 self.logger.debug(f"Found {len(direct_children)} direct children in <body>. Processing them.")
                 for tag in direct_children:
                     # Use process_tag_content to handle all tags uniformly
                     tag_content = self.process_tag_content(tag, config, f"Child <{tag.name}>", base_href)
                     self.structured_content.extend(tag_content)
            # --- MODIFICATION END ---

        except Exception as e:
            self.logger.error(f"Lỗi khi xử lý document item {item.get_name()}: {str(e)}", exc_info=True)


class EPUBProcessor:
    """Lớp chính để xử lý file EPUB và xuất ra XML"""

    def __init__(self, epub_path: str, config: Optional[ExtractorConfig] = None):
        self.epub_path = Path(epub_path)
        self.config = config or ExtractorConfig()

        self.base_dir = Path(self.config.output_dir)
        self.image_dir = self.base_dir / self.config.image_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.image_dir.mkdir(parents=True, exist_ok=True)

        self.setup_logging()

        self.image_processor = ImageProcessor(self.image_dir, self.logger)
        self.book: Optional[epub.EpubBook] = None
        self.content_processor: Optional[ContentProcessor] = None
        self.chapters: List[Tuple[str, str]] = []

    def setup_logging(self):
        """Thiết lập logging với encoding UTF-8"""
        log_file = self.base_dir / 'processing.log'
        log_format = '%(asctime)s - %(levelname)s - %(name)s - %(filename)s:%(lineno)d - %(message)s'
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        logging.basicConfig(
            level=logging.DEBUG, # Keep DEBUG for detailed analysis
            format=log_format,
            handlers=[
                logging.FileHandler(log_file, mode='w', encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Logging đã được thiết lập. Log file: {log_file}")


    def extract_metadata(self) -> Dict[str, str]:
        """Trích xuất metadata từ EPUB (không thêm vào XML theo format yêu cầu)"""
        if not self.book:
            self.logger.error("Book not loaded, cannot extract metadata.")
            return {}
        def get_meta(name: str) -> str:
            meta = self.book.get_metadata('DC', name)
            if meta and meta[0] and isinstance(meta[0], tuple):
                 return str(meta[0][0]) if len(meta[0]) > 0 else "Không có thông tin"
            elif meta:
                 return str(meta[0])
            return "Không có thông tin"
        fields = ['title', 'creator', 'publisher', 'date', 'language', 'identifier']
        metadata = {field.capitalize(): get_meta(field) for field in fields}
        self.logger.info(f"Metadata đã trích xuất: {metadata}")
        return metadata

    def extract_chapters(self):
        """Trích xuất danh sách các chương từ tài liệu điều hướng (Robust version)"""
        # --- Using the robust version from previous successful run ---
        if not self.book:
            self.logger.error("Không thể trích xuất chương, sách EPUB chưa được tải.")
            return
        nav_item = None
        is_ncx = False
        self.chapters = []
        try:
            nav_item_epub3 = None
            self.logger.info("Đang tìm tài liệu điều hướng EPUB 3 (thuộc tính 'nav')...")
            for item in self.book.get_items():
                if 'nav' in getattr(item, 'properties', []):
                    nav_item_epub3 = item
                    self.logger.info(f"Tìm thấy mục có thuộc tính 'nav': {item.get_name()}")
                    break
            if nav_item_epub3:
                nav_item = nav_item_epub3
                self.logger.info(f"Sử dụng tài liệu điều hướng EPUB 3: {nav_item.get_name()}")
            else:
                self.logger.info("Không tìm thấy mục EPUB 3 Nav. Đang tìm NCX EPUB 2 (ITEM_NAVIGATION)...")
                nav_items_ncx = list(self.book.get_items_of_type(ebooklib.ITEM_NAVIGATION))
                if nav_items_ncx:
                    nav_item = nav_items_ncx[0]
                    is_ncx = True
                    self.logger.info(f"Tìm thấy và sử dụng tài liệu điều hướng NCX EPUB 2: {nav_item.get_name()}")
                else:
                    self.logger.warning("Không tìm thấy tài liệu điều hướng EPUB 3 (thuộc tính 'nav') hay EPUB 2 NCX (ITEM_NAVIGATION).")
        except Exception as e:
            self.logger.error(f"Lỗi xảy ra trong quá trình tìm kiếm tài liệu điều hướng: {e}", exc_info=True)
        if not nav_item:
            self.logger.error("Không thể tìm thấy tài liệu điều hướng nào. Không thể trích xuất chương.")
            return
        try:
            self.logger.info(f"Đang xử lý tài liệu điều hướng: {nav_item.get_name()} (Loại: {'NCX' if is_ncx else 'XHTML'})")
            content = nav_item.get_content()
            nav_doc_base_href = urllib.parse.unquote(nav_item.get_name())
            if is_ncx:
                soup = BeautifulSoup(content, 'xml')
                for nav_point in soup.find_all('navPoint'):
                    nav_label = nav_point.find('navLabel')
                    title = nav_label.find('text').get_text().strip() if nav_label and nav_label.find('text') else f"Điểm NCX không tên {len(self.chapters) + 1}"
                    content_tag = nav_point.find('content')
                    if content_tag and content_tag.get('src'):
                        href_raw = content_tag['src']
                        href_resolved = urllib.parse.urljoin(nav_doc_base_href, href_raw)
                        normalized_href = urllib.parse.unquote(href_resolved).split('#')[0]
                        normalized_href = str(Path(normalized_href)).replace('\\', '/').lstrip('./').lstrip('../')
                        if normalized_href:
                            self.chapters.append((normalized_href, title))
                            self.logger.debug(f"Thêm chương (NCX): '{title}' -> '{normalized_href}' (Raw: '{href_raw}')")
                        else:
                            self.logger.warning(f"Bỏ qua navPoint NCX với src trống hoặc không hợp lệ: {href_raw}")
                    else:
                         self.logger.debug(f"Bỏ qua navPoint NCX không có content src hợp lệ: {nav_point.get('id', 'no id')}")
            else: # XHTML Nav Doc
                soup = BeautifulSoup(content, self.config.parser)
                nav_container = soup.find('nav', attrs={'epub:type': 'toc'}) or soup.find('nav') or soup.body
                if nav_container:
                    for link in nav_container.find_all('a', href=True):
                        href_raw = link['href']
                        if not href_raw or href_raw.startswith('#'):
                             self.logger.debug(f"Bỏ qua liên kết nav với href trống hoặc chỉ là fragment: {href_raw}")
                             continue
                        href_resolved = urllib.parse.urljoin(nav_doc_base_href, href_raw)
                        normalized_href = urllib.parse.unquote(href_resolved).split('#')[0]
                        normalized_href = str(Path(normalized_href)).replace('\\', '/').lstrip('./').lstrip('../')
                        if normalized_href:
                            link_text = ' '.join(link.stripped_strings) or f"Liên kết không tên {len(self.chapters) + 1}"
                            self.chapters.append((normalized_href, link_text))
                            self.logger.debug(f"Thêm chương (XHTML): '{link_text}' -> '{normalized_href}' (Raw: '{href_raw}')")
                        else:
                            self.logger.warning(f"Bỏ qua liên kết nav với href trống sau khi chuẩn hóa: {href_raw}")
                else:
                    self.logger.warning(f"Không tìm thấy thẻ chứa điều hướng phù hợp trong {nav_item.get_name()}")
        except Exception as e:
            self.logger.error(f"Lỗi khi phân tích nội dung tài liệu điều hướng {nav_item.get_name()}: {str(e)}", exc_info=True)
        self.logger.info(f"Trích xuất được {len(self.chapters)} chương từ điều hướng.")


    def process_content(self):
        """Process EPUB content items based on spine order and add to structured list."""
        if not self.book or not self.content_processor:
            self.logger.error("Book or ContentProcessor not initialized.")
            return
        self.logger.info("Bắt đầu xử lý nội dung theo thứ tự spine...")
        processed_hrefs = set()
        chapter_href_map = {href: title for href, title in self.chapters}
        spine_order_ids = self.book.spine
        for item_identifier, _ in spine_order_ids:
            item = self.book.get_item_with_href(item_identifier)
            if not item:
                self.logger.warning(f"Could not find item with identifier '{item_identifier}' (href) from spine. Trying by ID...")
                item_by_id = self.book.get_item_with_id(item_identifier)
                if item_by_id:
                    item = item_by_id
                    self.logger.info(f"Found item by ID instead: {item_identifier}")
                else:
                    self.logger.error(f"Failed to retrieve item for spine identifier: {item_identifier}. Skipping.")
                    continue
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                item_href_raw = item.get_name()
                item_href_normalized = urllib.parse.unquote(item_href_raw).split('#')[0]
                item_href_normalized = str(Path(item_href_normalized)).replace('\\', '/').lstrip('./').lstrip('../')
                if item_href_normalized in processed_hrefs:
                    self.logger.debug(f"Skipping already processed item: {item_href_normalized} (Raw: {item_href_raw})")
                    continue
                self.logger.info(f"Processing spine item: {item_href_normalized} (Raw: {item_href_raw}, ID: {item_identifier})")
                if item_href_normalized in chapter_href_map:
                    chapter_title = chapter_href_map[item_href_normalized]
                    self.logger.info(f"Chapter start detected: '{chapter_title}' for item {item_href_normalized}")
                    self.content_processor.structured_content.append({
                        "type": "chapter_start",
                        "title": chapter_title,
                        "href": item_href_normalized
                    })
                self.content_processor.process_document_item(item, self.config)
                processed_hrefs.add(item_href_normalized)
            else:
                self.logger.debug(f"Skipping non-document spine item: {item.get_name()} (ID: {item_identifier}, Type: {item.get_type()})")
        self.logger.info(f"Hoàn thành xử lý nội dung. Tìm thấy {len(self.content_processor.structured_content)} mục cấu trúc.")


    def save_results_xml(self):
        """Save processing results as a structured XML file based on structured_content."""
        if not self.content_processor:
            self.logger.error("Content processor not available, cannot save XML.")
            return
        if not self.content_processor.structured_content:
             self.logger.warning("Structured content list is empty. XML file will be minimal.")

        try:
            self.logger.info("Bắt đầu tạo file XML từ dữ liệu cấu trúc...")
            root = ET.Element("lightnovel")
            current_chapter_element = None
            chapter_count = 0
            paragraph_count = 0
            image_count = 0

            # --- Metadata block (remains commented out) ---
            # self.extract_metadata() # Call if needed, but don't add to XML per spec

            for item in self.content_processor.structured_content:
                item_type = item.get("type")
                self.logger.debug(f"Processing structured item for XML: Type='{item_type}'")

                if item_type == "chapter_start":
                    chapter_count += 1
                    current_chapter_element = ET.SubElement(
                        root, "chapter", id=f"ch{chapter_count}",
                        title=item.get("title", "Untitled Chapter")
                    )
                    self.logger.debug(f"  Created XML chapter: id='ch{chapter_count}', title='{item.get('title', 'Untitled')}'")

                elif item_type == "paragraph":
                    if current_chapter_element is None:
                        self.logger.warning(f"Found paragraph outside of a chapter context. Skipping text: '{item.get('text', '')[:50]}...'")
                        # Logic to handle orphan paragraphs (optional)
                        # if root.find('chapter') is None: # Create default if none exist
                        #     chapter_count += 1
                        #     current_chapter_element = ET.SubElement(root, "chapter", id=f"ch{chapter_count}", title="Orphan Content")
                        # elif root.findall('chapter'): # Add to last chapter if chapters exist
                        #     current_chapter_element = root.findall('chapter')[-1]
                        # else: # Skip if no chapters created yet
                        #     continue
                        continue # Skip orphan for now

                    paragraph_count += 1
                    para_elem = ET.SubElement(
                        current_chapter_element, "paragraph",
                        id=f"p{paragraph_count}", translate="yes"
                    )
                    if item.get("role"):
                        para_elem.set("role", item["role"])
                    text_elem = ET.SubElement(para_elem, "text")
                    # Text should already be cleaned by process_tag_content's flush_text
                    text_elem.text = item.get("text", "")
                    self.logger.debug(f"  Added XML paragraph: id='p{paragraph_count}' to chapter {current_chapter_element.get('id')}")

                elif item_type == "image":
                    if current_chapter_element is None:
                        self.logger.warning(f"Found image outside of a chapter context. Skipping image: {item.get('filename')}")
                        continue # Skip orphan image

                    image_count += 1
                    xml_file_path = self.base_dir / self.config.xml_filename
                    image_file_path = Path(item['filepath'])
                    try:
                        relative_image_path = os.path.relpath(image_file_path, start=xml_file_path.parent)
                        relative_image_path = relative_image_path.replace('\\', '/')
                    except ValueError:
                        self.logger.warning(f"Cannot create relative path for image {image_file_path} from {xml_file_path.parent}. Using default relative path.")
                        relative_image_path = f"{self.config.image_dir}/{item.get('filename', 'unknown_image')}"
                    ET.SubElement(
                        current_chapter_element, "image", id=f"img{image_count}",
                        src=relative_image_path, alt=item.get("alt", "Image")
                    )
                    self.logger.debug(f"  Added XML image: id='img{image_count}', src='{relative_image_path}' to chapter {current_chapter_element.get('id')}")
                else:
                     self.logger.warning(f"Unknown structured item type encountered: '{item_type}'. Skipping.")

            self.logger.info("Generating final XML output string...")
            xml_content_bytes = ET.tostring(root, encoding=self.config.output_encoding, method='xml')
            try:
                dom = minidom.parseString(xml_content_bytes)
                pretty_xml_content = dom.toprettyxml(indent="  ", encoding=self.config.output_encoding)
            except Exception as pretty_print_error:
                 self.logger.warning(f"Không thể định dạng XML (pretty-print), đang lưu phiên bản thô: {pretty_print_error}")
                 xml_declaration = f'<?xml version="1.0" encoding="{self.config.output_encoding}"?>\n'.encode(self.config.output_encoding)
                 pretty_xml_content = xml_declaration + xml_content_bytes
            xml_file = self.base_dir / self.config.xml_filename
            with open(xml_file, 'wb') as f:
                f.write(pretty_xml_content)
            self.logger.info(f"Đã lưu thành công nội dung XML vào: {xml_file}")
            self.save_image_log()
        except Exception as e:
            self.logger.error(f"Lỗi khi tạo hoặc lưu kết quả XML: {str(e)}", exc_info=True)

    def save_image_log(self):
        """Lưu log chi tiết về xử lý hình ảnh."""
        if not self.image_processor.image_log:
             self.logger.info("Không có hình ảnh nào được xử lý hoặc lưu, bỏ qua image log.")
             return
        log_file = self.base_dir / 'image_log.txt'
        try:
            with open(log_file, 'w', encoding=self.config.output_encoding) as f:
                f.write("="*20 + " Image Processing Log " + "="*20 + "\n\n")
                for entry in self.image_processor.image_log:
                    f.write(f"File:       {entry.get('filename', 'N/A')}\n")
                    f.write(f"Saved Path: {entry.get('filepath', 'N/A')}\n")
                    f.write(f"Dimensions: {entry.get('dimensions', 'N/A')}\n")
                    f.write(f"Source Ctx: {entry.get('source', 'N/A')}\n")
                    f.write(f"Hash:       {entry.get('hash', 'N/A')}\n")
                    f.write("-" * 60 + "\n")
            self.logger.info(f"Image processing log đã được lưu vào: {log_file}")
        except Exception as e:
            self.logger.error(f"Lỗi khi lưu image log: {str(e)}")


    def process(self) -> bool:
        """Phương thức chính để xử lý EPUB"""
        try:
            self.logger.info(f"Đang xử lý EPUB: {self.epub_path}")
            if not self.epub_path.exists():
                 self.logger.error(f"File EPUB không tồn tại: {self.epub_path}")
                 return False
            self.book = epub.read_epub(str(self.epub_path))
            self.content_processor = ContentProcessor(
                self.book, self.image_processor, self.logger
            )
            self.extract_chapters()
            self.extract_metadata() # Extract metadata, but it's not added to XML by default
            self.process_content() # Populate structured_content list
            self.save_results_xml() # Generate XML from structured_content
            self.logger.info("Xử lý EPUB hoàn tất thành công")
            return True
        except ebooklib.epub.EpubException as e:
            self.logger.error(f"Lỗi phân tích EPUB: {str(e)}", exc_info=True)
            return False
        except Exception as e:
            self.logger.error(f"Lỗi không xác định trong quá trình xử lý EPUB: {str(e)}", exc_info=True)
            return False

def main():
    """Ví dụ sử dụng"""
    epub_path = Path(r"C:\Users\Fubuki\Downloads\annas-arch-46138fa6bccb.epub")
    # epub_path = Path("./path/to/your/ebook.epub")

    config = ExtractorConfig(
        output_dir="output_extract_fixed_v2", # Yet another new directory
        image_dir="images",
        min_image_width=128,
        min_image_height=128,
        quality=95,
        xml_filename="lightnovel_v4.xml" # New XML filename
    )

    processor = EPUBProcessor(epub_path, config)
    success = processor.process()

    if success:
        print(f"Processing successful! Output saved in '{config.output_dir}'")
    else:
        print(f"Processing failed. Check logs in '{config.output_dir}/processing.log'")

if __name__ == "__main__":
    main()