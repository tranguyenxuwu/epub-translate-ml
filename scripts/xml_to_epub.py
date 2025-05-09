import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import os
import base64
from pathlib import Path
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def create_epub_from_xml(xml_path, epub_path, output_dir):
    """Creates an EPUB file from a structured XML file."""
    logger.info(f"Starting EPUB creation from XML: {xml_path}")

    try:
        with open(xml_path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f, 'xml')
    except FileNotFoundError:
        logger.error(f"Error: XML file not found at {xml_path}")
        return
    except Exception as e:
        logger.error(f"Error reading or parsing XML file: {e}")
        return

    book = epub.EpubBook()

    # --- Metadata --- (Add more as needed)
    book.set_identifier('unique_id_placeholder') # Replace with actual ID
    book.set_title(soup.find('title').get_text() if soup.find('title') else 'Untitled Light Novel') # Example: Get title if available
    book.set_language('vi') # Assuming Vietnamese based on content
    book.add_author('Unknown Author') # Replace or extract author if available

    # --- Cover Image --- 
    cover_img_tag = soup.find('cover')
    cover_item = None
    cover_image_path_rel = None
    if cover_img_tag and cover_img_tag.find('image'):
        cover_image_path_rel = cover_img_tag.find('image').get('src')
        if cover_image_path_rel:
            # Construct absolute path relative to the XML file's directory
            xml_dir = Path(xml_path).parent
            cover_image_path_abs = xml_dir / cover_image_path_rel
            cover_image_path_abs = cover_image_path_abs.resolve()

            if cover_image_path_abs.exists():
                logger.info(f"Found cover image: {cover_image_path_abs}")
                # Read image content
                try:
                    with open(cover_image_path_abs, 'rb') as img_file:
                        cover_content = img_file.read()
                    
                    # Determine image type and create EpubImage item
                    img_filename = cover_image_path_abs.name
                    img_media_type = f'image/{cover_image_path_abs.suffix.lstrip(".")}' # e.g., image/jpeg
                    
                    cover_item = epub.EpubImage(
                        uid='cover_image',
                        file_name=f'images/{img_filename}', # Store in 'images' folder within EPUB
                        media_type=img_media_type,
                        content=cover_content
                    )
                    book.add_item(cover_item)
                    book.set_cover(f'images/{img_filename}', cover_content) # Set as EPUB cover
                    logger.info(f"Cover image '{img_filename}' added and set.")
                except Exception as e:
                    logger.error(f"Error processing cover image {cover_image_path_abs}: {e}")
            else:
                logger.warning(f"Cover image specified in XML not found at {cover_image_path_abs}")
        else:
            logger.warning("Cover tag found, but 'src' attribute is missing or empty in the image tag.")
    else:
        logger.warning("No cover tag found in the XML.")

    # --- Content Processing --- 
    chapters = []
    spine = ['nav'] # Start spine with nav
    if cover_item:
        spine.append('cover') # Add cover page to spine if it exists
        
    # Process content - needs refinement based on actual XML structure
    # This is a basic example assuming chapters are direct children or within a specific tag
    # It needs to handle the mixed structure seen in the example (paragraphs then chapters)
    
    # Example: Process paragraphs directly under <lightnovel> first (like TOC)
    toc_content = "<h1>Table of Contents</h1>\n<ul>\n"
    toc_items = []
    for para in soup.lightnovel.find_all('paragraph', recursive=False):
        text_tag = para.find('text')
        if text_tag:
            text = text_tag.get_text(strip=True)
            # Simple TOC item - needs linking later if possible
            toc_content += f"<li>{text}</li>\n"
            toc_items.append(text)
            
    toc_content += "</ul>"
    if toc_items:
        toc_chapter = epub.EpubHtml(title='Table of Contents', file_name='toc.xhtml', lang='vi')
        toc_chapter.content = toc_content
        book.add_item(toc_chapter)
        chapters.append(toc_chapter)
        spine.append(toc_chapter)
        logger.info("Generated basic Table of Contents page.")

    # Process <chapter> elements
    chapter_count = 1
    for chapter_tag in soup.lightnovel.find_all('chapter'):
        chapter_title = chapter_tag.get('title', f'Chapter {chapter_count}')
        chapter_id = chapter_tag.get('id', f'ch{chapter_count}')
        file_name = f'{chapter_id}.xhtml'
        
        logger.info(f"Processing chapter: {chapter_title} (ID: {chapter_id})")
        
        # Create chapter content (HTML)
        chapter_html_content = f"<h1>{chapter_title}</h1>\n"
        
        for element in chapter_tag.children:
            if element.name == 'image':
                img_src = element.get('src')
                img_alt = element.get('alt', 'Image')
                if img_src:
                    # Try to find and embed the image
                    img_path_rel = img_src
                    xml_dir = Path(xml_path).parent
                    img_path_abs = (xml_dir / img_path_rel).resolve()
                    img_filename = img_path_abs.name
                    img_epub_path = f'images/{img_filename}' # Path within EPUB
                    
                    if img_path_abs.exists():
                        try:
                            with open(img_path_abs, 'rb') as img_f:
                                img_content = img_f.read()
                            img_media_type = f'image/{img_path_abs.suffix.lstrip(".")}'
                            img_item = epub.EpubImage(
                                uid=f'img_{element.get("id", img_filename)}',
                                file_name=img_epub_path,
                                media_type=img_media_type,
                                content=img_content
                            )
                            book.add_item(img_item)
                            chapter_html_content += f'<img src="{img_epub_path}" alt="{img_alt}"/>\n'
                            logger.debug(f"Added image {img_filename} to chapter {chapter_id}")
                        except Exception as e:
                            logger.error(f"Error processing image {img_path_abs} for chapter {chapter_id}: {e}")
                            chapter_html_content += f'<p>[Error loading image: {img_src}]</p>\n'
                    else:
                        logger.warning(f"Image not found for chapter {chapter_id}: {img_path_abs}")
                        chapter_html_content += f'<p>[Image not found: {img_src}]</p>\n'
                else:
                     logger.warning(f"Image tag in chapter {chapter_id} has no src attribute.")
                     
            elif element.name == 'paragraph':
                text_tag = element.find('text')
                if text_tag:
                    para_text = text_tag.get_text()
                    # Basic paragraph handling, consider preserving whitespace/formatting if needed
                    chapter_html_content += f"<p>{para_text.strip()}</p>\n"
            # Add handling for other potential elements within a chapter if necessary

        # Create and add chapter item
        epub_chapter = epub.EpubHtml(title=chapter_title, file_name=file_name, lang='vi')
        epub_chapter.content = chapter_html_content
        book.add_item(epub_chapter)
        chapters.append(epub_chapter)
        spine.append(epub_chapter)
        chapter_count += 1

    # --- Navigation (TOC) --- 
    book.toc = chapters # Use the list of EpubHtml items for TOC
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # --- Spine --- 
    book.spine = spine

    # --- Write EPUB file --- 
    epub_file_path = Path(output_dir) / epub_path
    epub_file_path.parent.mkdir(parents=True, exist_ok=True) # Ensure output directory exists
    try:
        epub.write_epub(str(epub_file_path), book, {})
        logger.info(f"EPUB file successfully created at: {epub_file_path}")
    except Exception as e:
        logger.error(f"Error writing EPUB file: {e}")

if __name__ == '__main__':
    # Get the directory where the script is located
    script_dir = Path(__file__).parent.resolve()
    
    # Define input XML path relative to the script directory
    xml_input_path = script_dir / 'output_1/lightnovel_content_translated.xml'
    
    # Define output EPUB path relative to the script directory's 'output' subfolder
    epub_output_filename = 'lightnovel_output.epub'
    output_directory = script_dir / 'output'

    # Ensure paths are absolute for clarity
    xml_input_path_abs = xml_input_path.resolve()
    output_directory_abs = output_directory.resolve()

    create_epub_from_xml(str(xml_input_path_abs), epub_output_filename, str(output_directory_abs))