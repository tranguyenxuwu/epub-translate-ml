import streamlit as st
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from PIL import Image
import html

# Only set page config if running as main
def configure_page():
    """Configure Streamlit page if not already configured"""
    try:
        st.set_page_config(
            page_title="Light Novel Preview", 
            page_icon="📖", 
            layout="wide",
            initial_sidebar_state="expanded"
        )
    except:
        # Page config already set
        pass

def load_xml_content(xml_path):
    """Load and parse the XML content"""
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        return root
    except Exception as e:
        st.error(f"Error loading XML file: {e}")
        return None

def load_translation_progress(json_path):
    """Load the translation progress JSON"""
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        st.error(f"Error loading translation progress: {e}")
        return {}

def parse_xml_structure(xml_root, translation_dict):
    """Parse XML structure and match with translations"""
    chapters = []
    
    for chapter_elem in xml_root.findall('chapter'):
        chapter_id = chapter_elem.get('id', '')
        chapter_title = chapter_elem.get('title', '')
        
        # Look for translated chapter title
        translated_title = None
        for key, value in translation_dict.items():
            if key.endswith('_title') and value == chapter_title:
                # Find the corresponding translated title
                ch_num = key.split('_')[0]  # e.g., 'ch1' from 'ch1_title'
                translated_title = translation_dict.get(f"{ch_num}_title", chapter_title)
                break
        
        chapter = {
            'id': chapter_id,
            'title': chapter_title,
            'translated_title': translated_title or chapter_title,
            'items': []
        }
        
        # Process chapter content (paragraphs and images)
        for item in chapter_elem:
            if item.tag == 'paragraph':
                para_id = item.get('id', '')
                para_role = item.get('role', '')
                text_elem = item.find('text')
                original_text = text_elem.text if text_elem is not None else ''
                
                # Look for translation
                translated_text = translation_dict.get(para_id, original_text)
                
                chapter['items'].append({
                    'type': 'paragraph',
                    'id': para_id,
                    'role': para_role,
                    'original': original_text,
                    'translated': translated_text,
                    'is_translated': para_id in translation_dict
                })
                
            elif item.tag == 'image':
                img_id = item.get('id', '')
                img_src = item.get('src', '')
                img_alt = item.get('alt', '')
                
                chapter['items'].append({
                    'type': 'image',
                    'id': img_id,
                    'src': img_src,
                    'alt': img_alt
                })
        
        chapters.append(chapter)
    
    return chapters

def flatten_content_to_items(chapters, search_term=""):
    """Flatten all chapters into a single list of items for pagination"""
    all_items = []
    
    for chapter in chapters:
        # Add chapter header as an item
        all_items.append({
            'type': 'chapter_header',
            'chapter_id': chapter['id'],
            'title': chapter['title'],
            'translated_title': chapter['translated_title']
        })
        
        # Add chapter content items
        for item in chapter['items']:
            if search_term:
                # Filter based on search term
                if item['type'] == 'paragraph':
                    if (search_term.lower() in item['original'].lower() or 
                        search_term.lower() in item['translated'].lower()):
                        all_items.append(item)
                else:
                    # Always include images when searching
                    all_items.append(item)
            else:
                all_items.append(item)
    
    return all_items

def paginate_items(items, page_size, current_page):
    """Paginate items and return the current page items"""
    start_idx = current_page * page_size
    end_idx = start_idx + page_size
    return items[start_idx:end_idx]

def display_item(item, show_mode, show_paragraph_ids, show_images, output_dir):
    """Display a single item (chapter header, paragraph, or image)"""
    if item['type'] == 'chapter_header':
        # Chapter title
        if show_mode == "both":
            st.markdown(f"# Original: {item['title']}")
            if item['translated_title'] != item['title']:
                st.markdown(f"# Translated: {item['translated_title']}")
        elif show_mode == "translated_only":
            if item['translated_title'] != item['title']:
                # Show translated title with click to toggle to original
                escaped_title = escape_for_tooltip(item['title'])
                escaped_translated = escape_for_tooltip(item['translated_title'])
                st.markdown(f'''
                <h1>
                    <span id="title_{item['chapter_id']}" 
                          onclick="toggleText('title_{item['chapter_id']}', '{escaped_translated}', '{escaped_title}')" 
                          style="cursor: pointer; text-decoration: underline;">
                        {item["translated_title"]}
                    </span>
                </h1>
                ''', unsafe_allow_html=True)
            else:
                st.markdown(f"# {item['title']}")
        else:
            st.markdown(f"# {item['title']}")
        st.markdown("---")
        
    elif item['type'] == 'paragraph':
        display_paragraph(item, show_mode, show_paragraph_ids)
        
    elif item['type'] == 'image' and show_images:
        display_image(item['src'], output_dir, item['alt'])
        st.markdown("<br>", unsafe_allow_html=True)

def display_image(img_src, output_dir, img_alt=""):
    """Display an image with error handling"""
    try:
        img_path = output_dir / img_src
        if img_path.exists():
            image = Image.open(img_path)
            st.image(image, use_column_width=True)
        else:
            st.warning(f"Image not found: {img_src}")
    except Exception as e:
        st.error(f"Error displaying image {img_src}: {e}")

def display_paragraph(item, show_original, show_ids):
    """Display a paragraph with original/translated text"""
    para_id = item['id']
    role = item['role']
    original = item['original']
    translated = item['translated']
    is_translated = item['is_translated']
    
    # Style based on role
    if role in ['h1', 'h2', 'h3']:
        if show_original == "both":
            st.markdown(f"### Original: {original}")
            if is_translated and translated != original:
                st.markdown(f"### Translated: {translated}")
        elif show_original == "original_only":
            st.markdown(f"### {original}")
        else:  # translated_only
            if is_translated and translated != original:
                # Show translated header with click to toggle to original
                escaped_original = escape_for_tooltip(original)
                escaped_translated = escape_for_tooltip(translated)
                unique_id = f"header_{para_id}"
                st.markdown(f'''
                <h3>
                    <span id="{unique_id}" 
                          onclick="toggleText('{unique_id}', '{escaped_translated}', '{escaped_original}')" 
                          style="cursor: pointer; text-decoration: underline;">
                        {translated}
                    </span>
                </h3>
                ''', unsafe_allow_html=True)
            else:
                st.markdown(f"### {original}")
    else:
        # Regular paragraph
        if show_ids:
            col1, col2 = st.columns([1, 10])
            with col1:
                color = "🟢" if is_translated else "🔴"
                st.caption(f"{color} `{para_id}`")
            with col2:
                # Check if it's dialogue
                is_dialogue = (original.startswith('"') and original.endswith('"')) or \
                             (translated.startswith('"') and translated.endswith('"')) or \
                             (original.startswith('(') and original.endswith(')')) or \
                             (translated.startswith('(') and translated.endswith(')'))
                
                if show_original == "both":
                    st.markdown(f"**Original:** {original}")
                    if translated and translated != original:
                        if is_dialogue:
                            st.markdown(f"**Translated:** *{translated}*")
                        else:
                            st.markdown(f"**Translated:** {translated}")
                    else:
                        st.markdown("*No translation available*")
                elif show_original == "original_only":
                    if is_dialogue:
                        st.markdown(f"*{original}*")
                    else:
                        st.markdown(original)
                else:  # translated_only
                    if is_translated and translated != original:
                        # Show translated text with click to toggle to original
                        escaped_original = escape_for_tooltip(original)
                        escaped_translated = escape_for_tooltip(translated)
                        unique_id = f"para_{para_id}"
                        if is_dialogue:
                            st.markdown(f'''
                            <span id="{unique_id}" 
                                  onclick="toggleText('{unique_id}', '{escaped_translated}', '{escaped_original}')" 
                                  style="cursor: pointer; text-decoration: underline; font-style: italic;">
                                {translated}
                            </span>
                            ''', unsafe_allow_html=True)
                        else:
                            st.markdown(f'''
                            <span id="{unique_id}" 
                                  onclick="toggleText('{unique_id}', '{escaped_translated}', '{escaped_original}')" 
                                  style="cursor: pointer; text-decoration: underline;">
                                {translated}
                            </span>
                            ''', unsafe_allow_html=True)
                    else:
                        if is_dialogue:
                            st.markdown(f"*{original}*")
                        else:
                            st.markdown(original)
        else:
            # No IDs, just display the content directly
            is_dialogue = (original.startswith('"') and original.endswith('"')) or \
                         (translated.startswith('"') and translated.endswith('"')) or \
                         (original.startswith('(') and original.endswith(')')) or \
                         (translated.startswith('(') and translated.endswith(')'))
            
            if show_original == "both":
                st.markdown(f"**Original:** {original}")
                if translated and translated != original:
                    if is_dialogue:
                        st.markdown(f"**Translated:** *{translated}*")
                    else:
                        st.markdown(f"**Translated:** {translated}")
                else:
                    st.markdown("*No translation available*")
            elif show_original == "original_only":
                if is_dialogue:
                    st.markdown(f"*{original}*")
                else:
                    st.markdown(original)
            else:  # translated_only
                if is_translated and translated != original:
                    # Show translated text with original on hover
                    escaped_original = escape_for_tooltip(original)
                    if is_dialogue:
                        st.markdown(f'<span title="{escaped_original}" style="cursor: help;">*{translated}*</span>', unsafe_allow_html=True)
                    else:
                        st.markdown(f'<span title="{escaped_original}" style="cursor: help;">{translated}</span>', unsafe_allow_html=True)
                else:
                    if is_dialogue:
                        st.markdown(f"*{original}*")
                    else:
                        st.markdown(original)
    
    st.markdown("<br>", unsafe_allow_html=True)

def escape_for_tooltip(text):
    """Escape text for use in HTML title attribute"""
    return html.escape(text, quote=True)

def run_preview(output_dir_path=None, title="📖 Light Novel Translation Preview", show_navigation=True):
    """
    Run the preview with optional parameters
    
    Args:
        output_dir_path (str/Path): Path to output directory. Defaults to 'output'
        title (str): Title for the preview page
        show_navigation (bool): Whether to show navigation back to main app
    """
    if title:
        st.title(title)
        st.markdown("Preview your translated light novel content with original structure and images.")
    
    # Handle output directory
    if output_dir_path is None:
        output_dir = Path("output")
    else:
        output_dir = Path(output_dir_path)
    
    # Show navigation if requested
    if show_navigation:
        if st.button("← Back to Main Workflow"):
            st.switch_page("app.py")
    
    # Sidebar for file selection and options
    with st.sidebar:
        st.header("📁 File Selection")
        
        if not output_dir.exists():
            st.error("Output directory not found.")
            return
        
        # Find XML and JSON files
        xml_files = list(output_dir.glob("*.xml"))
        json_files = list(output_dir.glob("*progress.json"))
        
        if not xml_files:
            st.warning("No XML files found in the output directory.")
            st.info("Make sure you have converted EPUB to XML first.")
            return
        
        # File selectors
        selected_xml = st.selectbox(
            "Choose XML file:",
            xml_files,
            format_func=lambda x: x.name
        )
        
        selected_json = None
        if json_files:
            selected_json = st.selectbox(
                "Choose translation progress file:",
                [None] + json_files,
                format_func=lambda x: "None (Original only)" if x is None else x.name
            )
        else:
            st.info("No translation progress found. Showing original text only.")
        
        # Display options
        st.header("⚙️ Display Options")
        
        if selected_json:
            show_mode = st.radio(
                "Display mode:",
                ["translated_only", "both", "original_only"],
                format_func=lambda x: {
                    "translated_only": "Translated Only",
                    "both": "Original + Translated", 
                    "original_only": "Original Only"
                }[x]
            )
        else:
            show_mode = "original_only"
            st.info("Showing original text only (no translations)")
        
        show_paragraph_ids = st.checkbox("Show paragraph IDs", value=True)
        show_images = st.checkbox("Show images", value=True)
        dark_theme = st.checkbox("Dark background", value=False)
        
        # Pagination settings
        st.header("📄 Pagination")
        chunk_size = st.slider("Items per page:", min_value=10, max_value=500, value=200, step=10)
        
        # Search functionality
        st.header("🔍 Search")
        search_term = st.text_input("Search in content:")
    
    # Apply theme and styling
    if dark_theme:
        st.markdown("""
        <style>
        .main .block-container {
            background-color: #1e1e1e;
            color: #ffffff;
        }
        </style>
        """, unsafe_allow_html=True)
    
    # Add custom CSS for better readability
    st.markdown("""
    <style>
    .paragraph-text {
        line-height: 1.6;
        margin-bottom: 0.8em;
        font-size: 16px;
    }
    .chapter-title {
        font-size: 1.8em;
        font-weight: bold;
        margin: 1.2em 0 0.8em 0;
        color: #1f77b4;
        border-bottom: 2px solid #1f77b4;
        padding-bottom: 0.4em;
    }
    .dialogue {
        font-style: italic;
        color: #666;
    }
    .translated {
        background-color: #e8f5e8;
        padding: 0.2em;
        border-left: 3px solid #4CAF50;
        margin-left: 1em;
    }
    .main .block-container {
        max-width: 1200px;
        padding-top: 2rem;
        padding-bottom: 2rem;
    }
    h1 {
        font-size: 1.6em !important;
        margin-bottom: 0.5em !important;
    }
    .stMarkdown p {
        margin-bottom: 0.5em;
        line-height: 1.6;
    }
    </style>
    """, unsafe_allow_html=True)
    
    # Load and display content
    if selected_xml:
        xml_root = load_xml_content(selected_xml)
        translation_dict = {}
        
        if selected_json:
            translation_dict = load_translation_progress(selected_json)
        
        if xml_root is not None:
            # Parse XML structure with translations
            chapters = parse_xml_structure(xml_root, translation_dict)
            
            if not chapters:
                st.warning("No content found in XML file.")
                return
            
            # Show statistics
            total_paragraphs = sum(len([item for item in ch['items'] if item['type'] == 'paragraph']) for ch in chapters)
            total_images = sum(len([item for item in ch['items'] if item['type'] == 'image']) for ch in chapters)
            translated_count = sum(len([item for item in ch['items'] if item['type'] == 'paragraph' and item['is_translated']]) for ch in chapters)
            
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Total Chapters", len(chapters))
            with col2:
                st.metric("Total Paragraphs", total_paragraphs)
            with col3:
                st.metric("Translated", f"{translated_count}/{total_paragraphs}")
            with col4:
                st.metric("Images", total_images)
            
            # Progress bar
            if total_paragraphs > 0:
                progress = translated_count / total_paragraphs
                st.progress(progress)
                st.caption(f"Translation Progress: {progress:.1%}")
            
            st.markdown("---")
            
            # Flatten all content into a single list for pagination
            all_items = flatten_content_to_items(chapters, search_term)
            
            if not all_items:
                st.warning("No content matches your search criteria.")
                return
            
            # Pagination controls
            total_items = len(all_items)
            total_pages = (total_items - 1) // chunk_size + 1
            
            # Initialize page number in session state
            if 'current_page' not in st.session_state:
                st.session_state.current_page = 0
            
            # Create pagination controls
            col1, col2, col3, col4, col5 = st.columns([1, 1, 2, 1, 1])
            
            with col1:
                if st.button("⏮️ First", disabled=(st.session_state.current_page == 0)):
                    st.session_state.current_page = 0
                    st.rerun()
            
            with col2:
                if st.button("◀️ Previous", disabled=(st.session_state.current_page == 0)):
                    st.session_state.current_page -= 1
                    st.rerun()
            
            with col3:
                # Page selector
                new_page = st.selectbox(
                    "Page:",
                    range(total_pages),
                    index=st.session_state.current_page,
                    format_func=lambda x: f"Page {x+1} of {total_pages}"
                )
                if new_page != st.session_state.current_page:
                    st.session_state.current_page = new_page
                    st.rerun()
            
            with col4:
                if st.button("▶️ Next", disabled=(st.session_state.current_page >= total_pages - 1)):
                    st.session_state.current_page += 1
                    st.rerun()
            
            with col5:
                if st.button("⏭️ Last", disabled=(st.session_state.current_page >= total_pages - 1)):
                    st.session_state.current_page = total_pages - 1
                    st.rerun()
            
            # Show current page info
            start_item = st.session_state.current_page * chunk_size + 1
            end_item = min((st.session_state.current_page + 1) * chunk_size, total_items)
            st.caption(f"Showing items {start_item}-{end_item} of {total_items}")
            
            # Get current page items
            current_page_items = paginate_items(all_items, chunk_size, st.session_state.current_page)
            
            st.markdown("---")
            
            # Create a centered layout with narrower content
            col_left, col_center, col_right = st.columns([1, 3, 1])
            
            with col_center:
                # Display current page items
                for item in current_page_items:
                    display_item(item, show_mode, show_paragraph_ids, show_images, output_dir)
            
            # Repeat pagination controls at bottom
            st.markdown("---")
            col1, col2, col3, col4, col5 = st.columns([1, 1, 2, 1, 1])
            
            with col1:
                if st.button("⏮️ First ", disabled=(st.session_state.current_page == 0), key="first_bottom"):
                    st.session_state.current_page = 0
                    st.rerun()
            
            with col2:
                if st.button("◀️ Previous ", disabled=(st.session_state.current_page == 0), key="prev_bottom"):
                    st.session_state.current_page -= 1
                    st.rerun()
            
            with col3:
                st.caption(f"Page {st.session_state.current_page + 1} of {total_pages}")
            
            with col4:
                if st.button("▶️ Next ", disabled=(st.session_state.current_page >= total_pages - 1), key="next_bottom"):
                    st.session_state.current_page += 1
                    st.rerun()
            
            with col5:
                if st.button("⏭️ Last ", disabled=(st.session_state.current_page >= total_pages - 1), key="last_bottom"):
                    st.session_state.current_page = total_pages - 1
                    st.rerun()

def main():
    """Main function for standalone execution"""
    configure_page()
    run_preview()

if __name__ == "__main__":
    main()
