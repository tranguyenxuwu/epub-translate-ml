import os
import logging
from typing import List, Optional, Dict, Set, Tuple, Union, Any
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
    # New: XML output filename
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
                # Handle potential non-numeric values gracefully
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
        try:
            image_hash = self.compute_hash(image_data)
            
            # Find existing log entry if image was already processed (for path reuse)
            existing_log = next((log for log in self.image_log if log['hash'] == image_hash), None)
            if existing_log:
                 self.logger.debug(f"Image hash {image_hash} already processed. Reusing path: {existing_log['filepath']}")
                 # Return info needed for XML, even if file wasn't *newly* saved
                 return {
                     'filepath': existing_log['filepath'],
                     'filename': existing_log['filename'],
                     'dimensions': existing_log['dimensions'],
                     'hash': image_hash,
                     'source': source_info # Update context if needed
                 }

            # If not processed before, proceed with saving logic
            with Image.open(BytesIO(image_data)) as img:
                actual_width = width if width is not None else img.width
                actual_height = height if height is not None else img.height

                if (actual_width < config.min_image_width or
                    actual_height < config.min_image_height):
                    self.logger.info(f"Skipping image {source_info} due to dimensions {actual_width}x{actual_height} < minimum {config.min_image_width}x{config.min_image_height}")
                    return None

                # Generate filename relative to the image directory base
                img_extension = config.image_format.lower() if config.image_format != 'JPEG' else 'jpg'
                filename = f"{image_hash}_{actual_width}x{actual_height}.{img_extension}"
                filepath = self.image_dir / filename

                if filepath.exists():
                     self.logger.debug(f"Image file already exists: {filepath}. Skipping save operation.")
                     # Still need to add to log and processed set if somehow missed before
                     if image_hash not in self.processed_images:
                         pass # Proceed to log and add to set below
                     else: # Already logged and in set
                         return { # Return existing info
                            'filepath': str(filepath),
                            'filename': filename,
                            'dimensions': f"{actual_width}x{actual_height}",
                            'hash': image_hash,
                            'source': source_info
                        }
                else:
                    # Ensure directory exists right before saving
                    self.image_dir.mkdir(parents=True, exist_ok=True)

                    if img.mode in ('RGBA', 'LA', 'P'): # Added 'P' for palette modes
                        # Ensure conversion to RGB before checking alpha or saving
                        img = img.convert('RGBA') # Convert palette to RGBA first
                        background = Image.new('RGB', img.size, 'white')
                        background.paste(img, mask=img.split()[-1]) # Use alpha channel as mask
                        background.save(filepath, config.image_format, quality=config.quality)
                    elif img.mode == 'CMYK':
                         img = img.convert('RGB')
                         img.save(filepath, config.image_format, quality=config.quality)
                    else:
                        # Assume it can be saved directly or converted simply
                        img.convert('RGB').save(filepath, config.image_format, quality=config.quality)
                    self.logger.debug(f"Saved image to {filepath}")


                image_details = {
                    'filepath': str(filepath),
                    'filename': filename, # Just the filename
                    'dimensions': f"{actual_width}x{actual_height}",
                    'source': source_info,
                    'hash': image_hash
                }

                # Add to log and processed set only if it's genuinely new
                if image_hash not in self.processed_images:
                    self.image_log.append(image_details)
                    self.processed_images.add(image_hash)

                return image_details # Return details including the path

        except Exception as e:
            self.logger.error(f"Error saving image from source '{source_info}': {str(e)}", exc_info=True)
            return None


class ContentProcessor:
    """Handles content extraction and processing for XML output"""

    def __init__(self, book: epub.EpubBook, image_processor: ImageProcessor,
                 logger: logging.Logger):
        self.book = book
        self.image_processor = image_processor
        self.logger = logger
        # Stores structured content items (dicts)
        self.structured_content: List[Dict[str, Any]] = []
        self.current_chapter_href: Optional[str] = None


    def extract_image_data(self, href: str, base_href: Optional[str] = None) -> Optional[bytes]:
        """Extract image data from EPUB by href, considering relative paths"""
        try:
            if href.startswith('data:image'):
                header, encoded = href.split(',', 1)
                return base64.b64decode(encoded)

            # Resolve relative paths using the document's base href if provided
            absolute_href = href
            if base_href and not href.startswith(('http://', 'https://', '/')):
                 # Construct absolute path relative to the *directory* of the base_href item
                 base_path = Path(base_href).parent
                 absolute_href = str(base_path / href).replace('\\', '/') # Normalize separators

            decoded_href = urllib.parse.unquote(absolute_href)
            # Try various normalization attempts
            potential_hrefs = [
                decoded_href,
                absolute_href,
                href, # Original href
                decoded_href.lstrip('./').lstrip('../'),
                absolute_href.lstrip('./').lstrip('../'),
                href.lstrip('./').lstrip('../')
            ]
            
            # Try finding item using ebooklib's internal resolution first
            for test_href in potential_hrefs:
                item = self.book.get_item_with_href(test_href)
                if item:
                    self.logger.debug(f"Found image item for href '{href}' (resolved as '{test_href}')")
                    return item.get_content()
            
            # If not found, maybe it's relative to the *root*? ebooklib *should* handle this, but let's try.
            item_root_relative = self.book.get_item_with_href(href.lstrip('/'))
            if item_root_relative:
                 self.logger.debug(f"Found image item for href '{href}' (resolved as root relative '{href.lstrip('/')}')")
                 return item_root_relative.get_content()


            self.logger.warning(f"Could not find EPUB item for image href: '{href}' (Base: '{base_href}', Absolute attempt: '{absolute_href}')")
            return None
        except Exception as e:
            self.logger.error(f"Error extracting image data for href '{href}': {str(e)}")
            return None

    def process_image(self, tag: Tag, context: str, config: ExtractorConfig, base_href: Optional[str]) -> Optional[Dict[str, Any]]:
        """Process image tag, save image, return structured data for XML."""
        image_info = None
        if tag.name in ['img', 'image']:
            src = self.image_processor.get_source(tag)
            if src:
                # Pass the base_href of the document containing the tag
                image_data = self.extract_image_data(src, base_href)
                if image_data:
                    width, height = self.image_processor.get_dimensions(tag)
                    source_info = f"Context: {context}, Source Tag: <{tag.name} src='{src}'> in doc '{base_href}'"
                    
                    # Save returns a dict with path info if successful
                    saved_image_details = self.image_processor.save(
                        image_data, source_info, width, height, config)
                        
                    if saved_image_details:
                        # Create the structured data entry for this image
                        image_info = {
                            "type": "image",
                            "filepath": saved_image_details['filepath'], # Full path where saved
                            "filename": saved_image_details['filename'], # Filename part
                            "alt": f"Image from {context}" # Simple alt text
                        }
                else:
                    self.logger.warning(f"Could not extract image data for src='{src}' in context '{context}' from doc '{base_href}'")
            else:
                 self.logger.debug(f"Skipping tag <{tag.name}> in context '{context}' - no valid source attribute found.")

        return image_info


    def process_ruby(self, tag: Tag) -> str:
        """Process ruby text formatting (extracts base + phonetic)"""
        # Keep original ruby processing for text extraction
        if tag.name == 'ruby':
            base = tag.find('rb')
            phonetic = tag.find('rt')
            if base and phonetic:
                # Format for plain text output within paragraph
                return f"{base.get_text().strip()}({phonetic.get_text().strip()})"
        # Fallback for non-ruby or malformed ruby tags
        return tag.get_text().strip()

    def process_tag_content(self, tag: Tag, config: ExtractorConfig, context: str, base_href: str) -> List[Dict[str, Any]]:
        """Recursively process content of a tag (e.g., p, h*) including text, images, ruby."""
        content_items = []
        current_text_parts = []

        for elem in tag.contents:
            if isinstance(elem, Tag):
                # 1. Handle Images (img, svg>image)
                if elem.name in ['img', 'image']:
                    if image_info := self.process_image(elem, context, config, base_href):
                        # If there's pending text, add it as a paragraph first
                        if current_text_parts:
                             content_items.append({"type": "paragraph", "text": ' '.join(current_text_parts).strip()})
                             current_text_parts = []
                        # Add the image item
                        content_items.append(image_info)
                elif elem.name == 'svg':
                    # Look for <image> tags *inside* SVG
                    for svg_img in elem.find_all('image'):
                         if image_info := self.process_image(svg_img, f"{context} SVG", config, base_href):
                             if current_text_parts:
                                 content_items.append({"type": "paragraph", "text": ' '.join(current_text_parts).strip()})
                                 current_text_parts = []
                             content_items.append(image_info)
                # 2. Handle Ruby
                elif elem.name == 'ruby':
                     current_text_parts.append(self.process_ruby(elem))
                # 3. Handle simple formatting (like bold - though XML output doesn't use it yet)
                elif elem.name in ['b', 'strong', 'i', 'em']:
                     # Recursively get text, potentially preserving tags if needed later
                     current_text_parts.append(elem.get_text().strip()) # Simple text extraction for now
                # 4. Handle Line Breaks (convert to space or potentially paragraph break)
                elif elem.name == 'br':
                    # Treat <br> as a space for now, common practice
                    if current_text_parts and current_text_parts[-1] != ' ':
                        current_text_parts.append(' ')
                # 5. Recurse for other block/inline elements if necessary (e.g., span, div)
                #    For now, just get their text content.
                else:
                     # Generic handling for other tags: extract their text content.
                     # This might flatten nested structures more than desired in some cases.
                     tag_text = elem.get_text().strip()
                     if tag_text:
                         current_text_parts.append(tag_text)

            # Handle Plain Text Nodes
            elif isinstance(elem, str):
                text = elem.strip()
                if text:
                    current_text_parts.append(text)

        # Add any remaining text as a final paragraph item
        if current_text_parts:
            content_items.append({"type": "paragraph", "text": ' '.join(current_text_parts).strip()})

        # Filter out empty paragraph items
        return [item for item in content_items if item.get("type") != "paragraph" or item.get("text")]


    def process_document_item(self, item: ebooklib.epub.EpubHtml, config: ExtractorConfig):
        """Process a single EPUB document (HTML/XHTML) item."""
        try:
            # Get base href for resolving relative paths within this document
            base_href = urllib.parse.unquote(item.get_name())#.split('#')[0] # Keep full name for path calc

            soup = BeautifulSoup(item.get_content(), config.parser)

            # Check if this item corresponds to a known chapter start
            current_href_normalized = base_href.lstrip('./').lstrip('../').split('#')[0]
            if self.current_chapter_href == current_href_normalized:
                 # This document starts a chapter already marked.
                 # (Title already added by EPUBProcessor based on chapter list)
                 pass # Avoid adding duplicate title info here


            # Add Title element from HTML <title> if present (might be redundant with chapter title)
            # Optional: Decide if you want this in addition to chapter titles from TOC
            # html_title = soup.find('title')
            # if html_title and html_title.get_text().strip():
            #     self.structured_content.append({
            #         "type": "html_title",
            #         "text": html_title.get_text().strip()
            #     })

            # Process body content - find all direct children of body
            body = soup.find('body')
            if not body:
                self.logger.warning(f"No <body> tag found in document: {base_href}")
                return # Skip processing if no body

            for tag in body.find_all(recursive=False): # Process top-level elements in body
                 if tag.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                     # Headings are treated like paragraphs in the target XML structure
                     # Prepend the heading level? Or add a 'role' attribute?
                     # For now, just add the content items (text/images) found within
                     heading_content = self.process_tag_content(tag, config, f"Heading {tag.name}", base_href)
                     # Optionally mark the first paragraph as a heading?
                     if heading_content and heading_content[0]['type'] == 'paragraph':
                         heading_content[0]['role'] = tag.name # Mark as heading role
                     self.structured_content.extend(heading_content)

                 elif tag.name == 'p':
                      # Process paragraph content
                      paragraph_content = self.process_tag_content(tag, config, "Paragraph", base_href)
                      self.structured_content.extend(paragraph_content)

                 elif tag.name in ['img', 'image']:
                     # Process standalone images directly under body
                     if image_info := self.process_image(tag, "Standalone Body Image", config, base_href):
                         self.structured_content.append(image_info)

                 elif tag.name == 'svg':
                     # Process standalone SVGs (looking for contained images)
                     for img in tag.find_all('image'):
                         if image_info := self.process_image(img, "Standalone SVG Image", config, base_href):
                             self.structured_content.append(image_info)

                 elif tag.name in ['div', 'section', 'article']:
                      # Recursively process content within containers
                      # Treat content inside like it was directly under body
                      # This requires a recursive approach or flattening the structure first
                      self.logger.debug(f"Processing container <{tag.name}> in {base_href}")
                      container_content = self.process_tag_content(tag, config, f"Container <{tag.name}>", base_href)
                      self.structured_content.extend(container_content)

                 # Add handling for other relevant block elements like lists (ul, ol), tables, etc. if needed
                 # For now, they might be skipped or their text extracted by fallback in process_tag_content

        except Exception as e:
            self.logger.error(f"Error processing document item {item.get_name()}: {str(e)}", exc_info=True)


class EPUBProcessor:
    """Main EPUB processing class generating XML output"""

    def __init__(self, epub_path: str, config: Optional[ExtractorConfig] = None):
        self.epub_path = Path(epub_path)
        self.config = config or ExtractorConfig()

        self.base_dir = Path(self.config.output_dir)
        self.image_dir = self.base_dir / self.config.image_dir
        # Ensure creation happens early
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.image_dir.mkdir(parents=True, exist_ok=True)


        self.setup_logging() # Setup logging before other components

        self.image_processor = ImageProcessor(self.image_dir, self.logger)
        self.book: Optional[epub.EpubBook] = None
        self.content_processor: Optional[ContentProcessor] = None
        self.chapters: List[Tuple[str, str]] = [] # List of (normalized_href, title)

    def setup_logging(self):
        """Configure logging"""
        log_file = self.base_dir / 'processing.log'
        log_format = '%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'

        # Remove existing handlers to avoid duplication if called multiple times
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)

        logging.basicConfig(
            level=logging.INFO, # Or DEBUG for more verbose output
            format=log_format,
            handlers=[
                logging.FileHandler(log_file, mode='w'), # Overwrite log each run
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Logging initialized. Log file: {log_file}")


    def extract_metadata(self) -> Dict[str, str]:
        """Extract EPUB metadata"""
        if not self.book:
            return {}
        def get_meta(name: str) -> str:
            meta = self.book.get_metadata('DC', name)
            # Handle cases where metadata might have attributes or be empty
            if meta and meta[0] and isinstance(meta[0], tuple):
                 return str(meta[0][0]) if len(meta[0]) > 0 else "Not available"
            elif meta: # Handle simpler list cases if necessary
                 return str(meta[0])
            return "Not available"

        fields = ['title', 'creator', 'publisher', 'date', 'language', 'identifier']
        metadata = {field.capitalize(): get_meta(field) for field in fields}
        self.logger.info(f"Extracted Metadata: {metadata}")
        return metadata

    def extract_chapters(self):
        """Extract chapter list using standard EPUB navigation properties."""
        if not self.book:
            self.logger.error("Cannot extract chapters, EPUB book not loaded.")
            return

        nav_item = None
        is_ncx = False
        self.chapters = [] # Reset chapters list

        # 1. Try EPUB 3 Nav Document
        try:
            nav_items_epub3 = self.book.get_items_by_properties('nav')
            nav_items_list = list(nav_items_epub3)
            if nav_items_list:
                nav_item = nav_items_list[0]
                self.logger.info(f"Found EPUB 3 navigation document: {nav_item.get_name()}")
            else:
                self.logger.info("No EPUB 3 navigation item found (properties='nav').")
        except Exception as e:
            self.logger.warning(f"Error checking for EPUB 3 navigation document: {e}")

        # 2. Try EPUB 2 NCX
        if not nav_item:
            self.logger.info("Attempting to find EPUB 2 NCX navigation.")
            try:
                nav_items_ncx = self.book.get_items_of_type(ebooklib.ITEM_NAVIGATION)
                nav_items_list = list(nav_items_ncx)
                if nav_items_list:
                    nav_item = nav_items_list[0]
                    is_ncx = True
                    self.logger.info(f"Found EPUB 2 NCX navigation document: {nav_item.get_name()}")
                else:
                    self.logger.info("No EPUB 2 NCX navigation item found.")
            except Exception as e:
                self.logger.warning(f"Error checking for EPUB 2 NCX navigation document: {e}")

        # 3. Fallback Guessing (Less reliable)
        if not nav_item:
            self.logger.warning("No standard navigation item found. Falling back to filename guessing.")
            # Refined list of common paths/names
            nav_hrefs_to_try = [
                "nav.xhtml", "toc.xhtml", "navigation-documents.xhtml", "nav.html",
                "OEBPS/nav.xhtml", "OEBPS/toc.xhtml", "OEBPS/navigation-documents.xhtml", "OEBPS/nav.html",
                "OPS/nav.xhtml", "OPS/toc.xhtml", "OPS/navigation-documents.xhtml", "OPS/nav.html",
                "toc.ncx", "OEBPS/toc.ncx", "OPS/toc.ncx"
            ]
            for href in nav_hrefs_to_try:
                nav_item_guess = self.book.get_item_with_href(href)
                if nav_item_guess:
                    nav_item = nav_item_guess
                    self.logger.info(f"Found navigation document by guessing filename: {href}")
                    if href.lower().endswith('.ncx') or nav_item.get_type() == ebooklib.ITEM_NAVIGATION:
                        is_ncx = True
                    break

        # 4. Process the found navigation item
        if not nav_item:
            self.logger.error("FATAL: Could not find any navigation document. Chapter extraction failed.")
            # Optional: Fallback to linear spine order?
            # for item in self.book.spine:
            #    spine_item = self.book.get_item_with_href(item[0])
            #    if spine_item and spine_item.get_type() == ebooklib.ITEM_DOCUMENT:
            #         # Use filename or HTML title as chapter title?
            #         self.chapters.append((spine_item.get_name(), f"Chapter {len(self.chapters) + 1}"))
            return # Exit if no nav doc found

        try:
            self.logger.info(f"Processing navigation item: {nav_item.get_name()} (Type: {'NCX' if is_ncx else 'XHTML'})")
            content = nav_item.get_content()
            nav_doc_base_href = urllib.parse.unquote(nav_item.get_name())

            if is_ncx:
                soup = BeautifulSoup(content, 'xml') # Use 'xml' parser for NCX
                for nav_point in soup.find_all('navPoint'):
                    nav_label = nav_point.find('navLabel')
                    label = nav_label.find('text').get_text().strip() if nav_label and nav_label.find('text') else f"Untitled NCX Point {len(self.chapters) + 1}"
                    content_tag = nav_point.find('content')
                    if content_tag and content_tag.get('src'):
                        href_raw = content_tag['src']
                        # Resolve relative to NCX file location
                        href_resolved = urllib.parse.urljoin(nav_doc_base_href, href_raw)
                        # Normalize and remove fragment
                        normalized_href = urllib.parse.unquote(href_resolved).split('#')[0]
                        # Further normalize path separators and leading dots/slashes
                        normalized_href = str(Path(normalized_href)).replace('\\', '/').lstrip('./').lstrip('../')

                        if normalized_href:
                             self.chapters.append((normalized_href, label))
                             self.logger.debug(f"Added chapter (NCX): '{label}' -> '{normalized_href}' (Raw: '{href_raw}')")
                        else:
                            self.logger.warning(f"Skipping NCX navPoint with empty or invalid src: {href_raw}")

            else: # XHTML Nav Doc
                soup = BeautifulSoup(content, self.config.parser)
                # Prioritize nav[epub:type="toc"] > nav > body
                nav_container = soup.find('nav', attrs={'epub:type': 'toc'}) or soup.find('nav') or soup.body
                if nav_container:
                    for link in nav_container.find_all('a', href=True):
                        href_raw = link['href']
                        if not href_raw or href_raw.startswith('#'): # Skip empty or fragment-only hrefs
                             self.logger.debug(f"Skipping nav link with fragment-only or empty href: {href_raw}")
                             continue

                        # Resolve relative to the Nav XHTML file location
                        href_resolved = urllib.parse.urljoin(nav_doc_base_href, href_raw)
                        # Normalize and remove fragment
                        normalized_href = urllib.parse.unquote(href_resolved).split('#')[0]
                        # Further normalize path separators and leading dots/slashes
                        normalized_href = str(Path(normalized_href)).replace('\\', '/').lstrip('./').lstrip('../')


                        if normalized_href:
                            link_text = ' '.join(link.stripped_strings) or f"Untitled Link {len(self.chapters) + 1}"
                            self.chapters.append((normalized_href, link_text))
                            self.logger.debug(f"Added chapter (XHTML): '{link_text}' -> '{normalized_href}' (Raw: '{href_raw}')")
                        else:
                            self.logger.warning(f"Skipping nav link with effectively empty href after normalization: {href_raw}")
                else:
                    self.logger.warning(f"Could not find a suitable navigation container in {nav_item.get_name()}")

        except Exception as e:
            self.logger.error(f"Error parsing navigation document {nav_item.get_name()}: {str(e)}", exc_info=True)

        self.logger.info(f"Extracted {len(self.chapters)} chapters from navigation.")
        # Debug: Print extracted chapters
        # for i, (href, title) in enumerate(self.chapters):
        #     self.logger.debug(f"Chapter {i+1}: [{title}] -> {href}")


    def process_content(self):
        """Process EPUB content items based on spine order and chapter markers."""
        if not self.book or not self.content_processor:
            self.logger.error("Book or ContentProcessor not initialized.")
            return
        
        self.logger.info("Starting content processing...")
        
        processed_hrefs = set()
        chapter_href_map = {href: title for href, title in self.chapters}

        # Iterate through the spine to maintain reading order
        spine_items = self.book.get_spine_items()
        
        for item in spine_items:
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                 item_href_raw = item.get_name()
                 # Normalize href for matching chapter map and tracking processed items
                 item_href_normalized = urllib.parse.unquote(item_href_raw).split('#')[0]
                 item_href_normalized = str(Path(item_href_normalized)).replace('\\', '/').lstrip('./').lstrip('../')

                 if item_href_normalized in processed_hrefs:
                      self.logger.debug(f"Skipping already processed item: {item_href_normalized} (Raw: {item_href_raw})")
                      continue

                 self.logger.info(f"Processing spine item: {item_href_normalized} (Raw: {item_href_raw})")

                 # Check if this item marks the beginning of a chapter from the TOC
                 if item_href_normalized in chapter_href_map:
                     chapter_title = chapter_href_map[item_href_normalized]
                     self.logger.info(f"Chapter start detected: '{chapter_title}' for item {item_href_normalized}")
                     # Add chapter marker to structured content
                     self.content_processor.structured_content.append({
                         "type": "chapter_start",
                         "title": chapter_title,
                         "href": item_href_normalized # Store href for reference
                     })
                     # Update the content processor's current chapter context
                     self.content_processor.current_chapter_href = item_href_normalized

                 # Process the document content
                 self.content_processor.process_document_item(item, self.config)
                 processed_hrefs.add(item_href_normalized)
            else:
                 self.logger.debug(f"Skipping non-document spine item: {item.get_name()} (Type: {item.get_type()})")

        self.logger.info(f"Finished processing content. Found {len(self.content_processor.structured_content)} structured items.")


    def save_results_xml(self):
        """Save processing results as a structured XML file."""
        if not self.content_processor:
            self.logger.error("Content processor not available, cannot save XML.")
            return

        try:
            root = ET.Element("lightnovel")
            current_chapter_element = None
            chapter_count = 0
            paragraph_count = 0
            image_count = 0

            # Add Metadata to XML (Optional - Not in the requested format, but could be useful)
            # metadata_elem = ET.SubElement(root, "metadata")
            # for k, v in self.extract_metadata().items():
            #     ET.SubElement(metadata_elem, k.lower().replace(" ", "_")).text = v

            for item in self.content_processor.structured_content:
                item_type = item.get("type")

                if item_type == "chapter_start":
                    chapter_count += 1
                    # Reset paragraph/image counters for each chapter if IDs should be chapter-relative
                    # paragraph_count = 0 # Uncomment if p IDs reset per chapter
                    # image_count = 0     # Uncomment if img IDs reset per chapter
                    current_chapter_element = ET.SubElement(
                        root,
                        "chapter",
                        id=f"ch{chapter_count}",
                        title=item.get("title", "Untitled Chapter")
                    )
                elif item_type == "paragraph":
                     if current_chapter_element is None:
                          self.logger.warning("Found paragraph outside of a chapter, skipping: " + item.get('text', '')[:50] + "...")
                          # Or create a default chapter?
                          # if root.find('chapter') is None: # Create a default chapter if none exist yet
                          #     chapter_count += 1
                          #     current_chapter_element = ET.SubElement(root, "chapter", id=f"ch{chapter_count}", title="Default Chapter")
                          # else: # Append to the last chapter? Less ideal.
                          #      current_chapter_element = root.findall('chapter')[-1]
                          continue # Skip orphan paragraphs

                     paragraph_count += 1
                     para_elem = ET.SubElement(
                         current_chapter_element,
                         "paragraph",
                         id=f"p{paragraph_count}",
                         translate="yes" # As per requirement
                     )
                     # Add role attribute if present (e.g., for headings)
                     if item.get("role"):
                         para_elem.set("role", item["role"])

                     text_elem = ET.SubElement(para_elem, "text")
                     text_elem.text = item.get("text", "").strip()

                     # Future: Add chunking logic here if needed based on text length
                     # if len(text_elem.text) > 500: # Example threshold
                     #    para_elem.set("chunks", "3") # Example value

                elif item_type == "image":
                    if current_chapter_element is None:
                         self.logger.warning(f"Found image outside of a chapter, skipping: {item.get('filename')}")
                         # Handle orphan image similarly to paragraphs if needed
                         continue

                    image_count += 1
                    # Calculate relative path from XML file to image file
                    xml_file_path = self.base_dir / self.config.xml_filename
                    image_file_path = Path(item['filepath'])
                    try:
                        # Make path relative to the *directory* containing the XML file
                        relative_image_path = os.path.relpath(image_file_path, start=xml_file_path.parent)
                        # Ensure forward slashes for consistency in XML/web contexts
                        relative_image_path = relative_image_path.replace('\\', '/')
                    except ValueError:
                         # Happens if paths are on different drives on Windows
                         self.logger.warning(f"Cannot create relative path for image {image_file_path} from {xml_file_path.parent}. Using absolute path or filename.")
                         # Fallback: use filename only, assuming images are in a known relative dir like 'images/'
                         relative_image_path = f"{self.config.image_dir}/{item.get('filename', 'unknown_image')}"


                    ET.SubElement(
                        current_chapter_element,
                        "image",
                        id=f"img{image_count}",
                        src=relative_image_path,
                        alt=item.get("alt", "Image")
                    )
                # Handle other item types if necessary
                # elif item_type == "html_title":
                #     # Decide where to put this - maybe an attribute on the chapter?
                #     if current_chapter_element is not None:
                #          current_chapter_element.set("html_title", item.get("text", ""))

            # Generate XML string
            tree = ET.ElementTree(root)
            xml_content_bytes = ET.tostring(root, encoding=self.config.output_encoding, method='xml')

            # Pretty print using minidom
            try:
                dom = minidom.parseString(xml_content_bytes)
                pretty_xml_content = dom.toprettyxml(indent="  ", encoding=self.config.output_encoding)
            except Exception as pretty_print_error:
                 self.logger.warning(f"Failed to pretty-print XML, saving raw version: {pretty_print_error}")
                 # Fallback to non-pretty-printed bytes with XML declaration
                 xml_declaration = f'<?xml version="1.0" encoding="{self.config.output_encoding}"?>\n'.encode(self.config.output_encoding)
                 pretty_xml_content = xml_declaration + xml_content_bytes

            # Save XML file
            xml_file = self.base_dir / self.config.xml_filename
            with open(xml_file, 'wb') as f: # Write bytes
                f.write(pretty_xml_content)
            self.logger.info(f"Successfully saved XML content to: {xml_file}")

            # Save image log separately (optional, but useful for debugging)
            self.save_image_log()

        except Exception as e:
            self.logger.error(f"Error generating or saving XML results: {str(e)}", exc_info=True)

    def save_image_log(self):
        """Saves the detailed image processing log."""
        if not self.image_processor.image_log:
             self.logger.info("No images processed or saved, skipping image log.")
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
            self.logger.info(f"Image processing log saved to: {log_file}")
        except Exception as e:
            self.logger.error(f"Error saving image log: {str(e)}")


    def process(self) -> bool:
        """Main processing method"""
        try:
            self.logger.info(f"Processing EPUB: {self.epub_path}")
            if not self.epub_path.exists():
                 self.logger.error(f"EPUB file not found: {self.epub_path}")
                 return False

            self.book = epub.read_epub(str(self.epub_path))
            self.content_processor = ContentProcessor(
                self.book, self.image_processor, self.logger)

            # Extract chapters first to guide content processing
            self.extract_chapters()
            
            # Metadata extraction can happen anytime after book load
            # self.extract_metadata() # Metadata isn't added to XML structure currently

            # Process content based on spine and chapter markers
            self.process_content()

            # Save results as XML
            self.save_results_xml()

            self.logger.info("EPUB processing completed successfully")
            return True

        except ebooklib.epub.EpubException as e:
            self.logger.error(f"EPUB parsing error: {str(e)}", exc_info=True)
            return False
        except Exception as e:
            self.logger.error(f"Unhandled error during EPUB processing: {str(e)}", exc_info=True)
            return False

def main():
    """Example usage"""
    # Use raw string literal for Windows paths or Path object
    epub_path = Path(r"C:\Users\Fubuki\Downloads\gai_nga_v4_bak.epub")
    # epub_path = Path("./path/to/your/ebook.epub") # Example for relative path

    config = ExtractorConfig(
        output_dir="output_v4_xml", # Changed output directory
        image_dir="images",        # Relative to output_dir
        min_image_width=500,
        min_image_height=500,
        quality=90,                # Slightly lower quality for smaller files
        xml_filename="lightnovel_content.xml" # Customize XML filename
    )

    processor = EPUBProcessor(epub_path, config)
    success = processor.process()

    if success:
        print(f"Processing successful! Output saved in '{config.output_dir}'")
    else:
        print(f"Processing failed. Check logs in '{config.output_dir}/processing.log'")

if __name__ == "__main__":
    main()