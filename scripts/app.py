import streamlit as st
import os
from pathlib import Path
import logging
import json

# Import the main functions from your scripts
# Note: We will need to refactor the original scripts to make these functions available.
from epub_to_xml import EbookProcessor # Assuming EbookProcessor class encapsulates the logic
from translate_xml import XMLTranslator, stop_flag
from xml_to_epub import create_epub_from_xml

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="EPUB Translator", 
    layout="wide",
    initial_sidebar_state="expanded"
)

def detect_previous_translations(output_dir):
    """Detect and return information about previous translation progress"""
    progress_files = list(output_dir.glob("*progress.json"))
    xml_files = list(output_dir.glob("*_translated.xml"))
    
    detection_info = {
        'has_progress': len(progress_files) > 0,
        'has_translated_xml': len(xml_files) > 0,
        'progress_files': progress_files,
        'translated_xml_files': xml_files,
        'completion_percentage': 0,
        'translated_count': 0,
        'total_count': 0
    }
    
    if progress_files:
        try:
            with open(progress_files[0], 'r', encoding='utf-8') as f:
                progress_data = json.load(f)
                detection_info['translated_count'] = len(progress_data)
                
                # Try to get total count from original XML
                xml_files_for_total = list(output_dir.glob("*.xml"))
                xml_files_for_total = [f for f in xml_files_for_total if not f.name.endswith('_translated.xml')]
                
                if xml_files_for_total:
                    import xml.etree.ElementTree as ET
                    try:
                        tree = ET.parse(xml_files_for_total[0])
                        root = tree.getroot()
                        
                        total_elements = 0
                        for chapter in root.findall('.//chapter'):
                            if chapter.get('title'):
                                total_elements += 1  # Chapter title
                            for elem in chapter:
                                if elem.tag == 'paragraph' and elem.get('translate') == 'yes':
                                    total_elements += 1
                        
                        detection_info['total_count'] = total_elements
                        if total_elements > 0:
                            detection_info['completion_percentage'] = (detection_info['translated_count'] / total_elements) * 100
                    except Exception as e:
                        logger.warning(f"Could not determine total element count: {e}")
        except Exception as e:
            logger.warning(f"Could not read progress file: {e}")
    
    return detection_info

def main():
    # Add navigation
    st.sidebar.title("📚 EPUB Translator")
    
    # Create navigation
    page = st.sidebar.selectbox(
        "Navigate to:",
        ["🏠 Main Workflow", "📖 Translation Preview"],
        index=0
    )
    
    if page == "📖 Translation Preview":
        # Redirect to preview functionality
        try:
            import st_preview_content
            st_preview_content.main()
        except ImportError:
            st.error("Preview module not found. Please ensure st_preview_content.py is available.")
        except Exception as e:
            st.error(f"Error loading preview: {e}")
        return
    
    # Main workflow page
    st.title("EPUB to Translated EPUB Workflow")

    # --- Setup Directories ---
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    image_dir = output_dir / "images"
    image_dir.mkdir(exist_ok=True)

    # --- Step 1: Upload EPUB and Convert to XML ---
    st.header("Step 1: Upload EPUB and Convert to XML")
    uploaded_file = st.file_uploader("Choose an EPUB file", type="epub")

    if uploaded_file is not None:
        epub_path = output_dir / uploaded_file.name
        xml_path = output_dir / f"{epub_path.stem}.xml"

        # Save uploaded file to disk
        with open(epub_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        st.info(f"Saved EPUB to {epub_path}")

        if st.button("Convert EPUB to XML", key="convert_epub_btn"):
            with st.spinner("Processing EPUB... This may take a moment."):
                try:
                    # This assumes epub_to_xml.py is refactored into a class or function
                    processor = EbookProcessor(str(epub_path), str(output_dir))
                    result = processor.run()
                    
                    if isinstance(result, dict):
                        # New detailed result format
                        if result['success']:
                            st.success(f"Successfully converted EPUB to XML: {xml_path}")
                            
                            # Check for navigation/TOC warnings
                            if not result['has_navigation'] or result['chapters_found'] == 0:
                                st.warning("⚠️ **No navigation or table of contents found in the EPUB file.** "
                                          "The conversion proceeded, but the structure may not be optimal. "
                                          "This can happen with EPUBs that lack proper navigation documents "
                                          "(TableOfContents.xhtml, navigation-documents.xhtml, etc.). "
                                          "The translation will still work, but chapter organization may be affected.")
                            else:
                                st.info(f"✅ Found {result['chapters_found']} chapters in the navigation structure.")
                        else:
                            st.error("Failed to convert EPUB to XML. Check the logs for details.")
                    else:
                        # Legacy boolean result format (backward compatibility)
                        if result:
                            st.success(f"Successfully converted EPUB to XML: {xml_path}")
                        else:
                            st.error("Failed to convert EPUB to XML. Check the logs for details.")
                except Exception as e:
                    st.error(f"An error occurred during XML conversion: {e}")

    # --- Step 2: Translate the XML file ---
    st.header("Step 2: Translate XML Content")
    
    # Check for API key
    api_key = os.getenv("API_KEY")
    if not api_key:
        st.warning("⚠️ API_KEY not found in environment variables. Please set your API key to proceed with translation.")
        api_key_input = st.text_input("Enter your API Key:", type="password")
        if api_key_input:
            os.environ["API_KEY"] = api_key_input
            st.success("API Key set successfully!")
    
    # We need to find the generated XML file to proceed
    xml_files = list(output_dir.glob("*.xml"))
    if not xml_files:
        st.warning("No XML file found. Please complete Step 1.")
    else:
        # For simplicity, we'll just use the first XML file found.
        # A more robust app might let the user choose.
        xml_to_translate = xml_files[0]
        translated_xml_path = output_dir / f"{xml_to_translate.stem}_translated.xml"

        st.info(f"Ready to translate: {xml_to_translate.name}")
        
        # Custom prompt editing section
        st.subheader("Translation Settings")
        
        # Model selection
        col1, col2 = st.columns(2)
        with col1:
            model_options = [
                "deepseek/deepseek-r1-0528:free",
                "deepseek/deepseek-r1:free",
                "custom"
            ]
            
            # Get current model from session state or use default
            current_model = getattr(st.session_state, 'selected_model', model_options[0])
            default_index = 0
            if current_model in model_options:
                default_index = model_options.index(current_model)
            
            selected_model = st.selectbox(
                "Select Translation Model:",
                model_options,
                index=default_index,
                help="Choose the AI model for translation. Some models may require different API keys."
            )
            
            if selected_model == "custom":
                custom_model = st.text_input(
                    "Enter custom model name:",
                    value=current_model if current_model not in model_options else "",
                    placeholder="e.g., anthropic/claude-3-opus"
                )
                if custom_model:
                    selected_model = custom_model
        
        with col2:
            # Get current batch size from session state or use default
            current_batch_size = getattr(st.session_state, 'batch_size', 70)
            
            batch_size = st.number_input(
                "Batch Size:",
                min_value=10,
                max_value=200,
                value=current_batch_size,
                step=10,
                help="Number of text elements processed per API call. Smaller batches are more reliable but slower."
            )
        
        with st.expander("📝 Customize Translation Prompt", expanded=False):
            # Get the default prompt from translate_xml module
            from translate_xml import PROMPT_TEMPLATE
            
            # Use session state value if available, otherwise use default
            current_prompt = getattr(st.session_state, 'custom_prompt', PROMPT_TEMPLATE)
            
            custom_prompt = st.text_area(
                "Edit the translation prompt:",
                value=current_prompt,
                height=400,
                help="Modify this prompt to customize how the AI translates your content. The placeholders {context} and {content} will be filled automatically."
            )
            
            # Quick prompt templates
            st.write("**Quick Templates:**")
            template_col1, template_col2, template_col3 = st.columns(3)
            
            with template_col1:
                if st.button("📚 Light Novel (Default)", key="template_light_novel_btn"):
                    st.session_state.custom_prompt = PROMPT_TEMPLATE
                    st.rerun()
            
            with template_col2:
                if st.button("📰 General Text", key="template_general_btn"):
                    general_prompt = """Translate the following text into Vietnamese. Follow these instructions:
1. Maintain the original meaning and tone
2. Use natural, fluent Vietnamese
3. Preserve formatting and structure
4. Each input is in format id: text_to_translate
5. Return as id: translated_text

Content to translate:
{content}"""
                    st.session_state.custom_prompt = general_prompt
                    st.rerun()
            
            with template_col3:
                if st.button("🎭 Formal/Academic", key="template_formal_btn"):
                    formal_prompt = """Translate the following text into formal Vietnamese suitable for academic or professional contexts. Follow these instructions:
1. Use formal, polished language
2. Maintain technical terminology accuracy
3. Preserve original structure and meaning
4. Each input is in format id: text_to_translate
5. Return as id: translated_text

Content to translate:
{content}"""
                    st.session_state.custom_prompt = formal_prompt
                    st.rerun()
            
            if st.button("💾 Save Custom Prompt", key="save_prompt_btn"):
                # Store the custom prompt in session state
                st.session_state.custom_prompt = custom_prompt
                st.session_state.selected_model = selected_model
                st.session_state.batch_size = batch_size
                st.success("Settings saved!")
        
        
        # Check for previous translations
        detection_info = detect_previous_translations(output_dir)
        
        if detection_info['has_progress']:
            st.info(f"📋 **Previous translation detected!** "
                   f"Found {detection_info['translated_count']} translated elements "
                   f"({detection_info['completion_percentage']:.1f}% complete). "
                   f"Translation will resume from where it left off.")
        
        if st.button("Translate XML", key="translate_xml_btn"):
            if not os.getenv("API_KEY"):
                st.error("Please provide an API key before translating.")
            else:
                # Initialize session state for translation control
                if 'translation_running' not in st.session_state:
                    st.session_state.translation_running = False
                if 'stop_requested' not in st.session_state:
                    st.session_state.stop_requested = False
                
                # Set running state and reset stop flag
                st.session_state.translation_running = True
                st.session_state.stop_requested = False
                
                # Reset stop flag in translate_xml module
                import translate_xml
                translate_xml.stop_flag = False
                
                # Create container for dynamic content
                translation_container = st.container()
                
                with translation_container:
                    try:
                        # Create translator instance
                        translator = XMLTranslator(str(xml_to_translate))
                        
                        # Reset any previous interrupt flags
                        translator.reset_interrupt_flag()
                        
                        # Apply custom settings if available
                        if hasattr(st.session_state, 'custom_prompt') and st.session_state.custom_prompt:
                            translator.set_custom_prompt(st.session_state.custom_prompt)
                        
                        if hasattr(st.session_state, 'selected_model') and st.session_state.selected_model:
                            translator.set_model(st.session_state.selected_model)
                        
                        if hasattr(st.session_state, 'batch_size') and st.session_state.batch_size:
                            translator.set_batch_size(st.session_state.batch_size)
                        
                        # Get initial progress info
                        progress_info = translator.get_progress_info()
                        
                        if progress_info['total_elements'] > 0:
                            st.info(f"📊 Translation Progress: {progress_info['translated_elements']}/{progress_info['total_elements']} "
                                   f"({progress_info['completion_percentage']:.1f}% complete)")
                            
                            # Create progress bar and status
                            progress_bar = st.progress(progress_info['completion_percentage'] / 100)
                            status_text = st.empty()
                            
                            status_text.text(f"Starting translation... {progress_info['remaining_elements']} elements remaining")
                        
                        # Create a placeholder for the stop button
                        stop_placeholder = st.empty()
                        
                        # Show stop button
                        with stop_placeholder:
                            col1, col2 = st.columns([1, 3])
                            with col1:
                                if st.button("🛑 Stop Translation", key="stop_translate_btn", help="Stop translation after current batch"):
                                    st.session_state.stop_requested = True
                                    translate_xml.stop_flag = True
                                    translator.stop_translation()
                                    st.warning("⏸️ Stop signal sent! Translation will finish current batch and save progress...")
                                    # Hide the stop button and show confirmation
                                    stop_placeholder.empty()
                                    st.error("🛑 Translation stopping... Please wait for current batch to complete.")
                                    st.session_state.translation_running = False
                                    return  # Exit early
                        
                        # Check if stop was requested before starting
                        if not st.session_state.stop_requested:
                            # Run translation with progress updates
                            with st.spinner("Translating content via API... This can take a long time."):
                                translator.run()
                        
                        # Clear the stop button after translation
                        stop_placeholder.empty()
                        
                        # Final progress check
                        final_progress = translator.get_progress_info()
                        
                        if st.session_state.stop_requested or translate_xml.stop_flag:
                            st.warning(f"⏸️ Translation stopped by user. Progress saved: {final_progress['translated_elements']}/{final_progress['total_elements']} elements completed. You can resume by clicking 'Translate XML' again.")
                        elif final_progress['completion_percentage'] >= 100:
                            st.success(f"✅ Translation completed successfully! Translated {final_progress['translated_elements']} elements.")
                        else:
                            st.info(f"📊 Translation progress: {final_progress['translated_elements']}/{final_progress['total_elements']} elements completed.")
                            
                    except ValueError as e:
                        st.error(f"Configuration error: {e}")
                    except Exception as e:
                        st.error(f"An error occurred during translation: {e}")
                        import traceback
                        st.error(f"Detailed error: {traceback.format_exc()}")
                    finally:
                        # Reset translation state
                        st.session_state.translation_running = False

    # --- Step 2.5: Preview Translated Content ---
    st.header("📖 Translation Preview")
    
    # Check for translated content
    detection_info = detect_previous_translations(output_dir)
    
    if detection_info['has_progress']:
        st.info(f"📚 Translation progress found: {detection_info['translated_count']} elements translated "
               f"({detection_info['completion_percentage']:.1f}% complete)")
        
        # Create navigation to preview
        st.markdown("### View Translation Preview")
        st.markdown("*Use the sidebar navigation to switch to 'Translation Preview' to view your translated content.*")
        
        # Quick preview statistics
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Translated Elements", detection_info['translated_count'])
        with col2:
            st.metric("Progress", f"{detection_info['completion_percentage']:.1f}%")
        with col3:
            if detection_info['total_count'] > 0:
                st.metric("Total Elements", detection_info['total_count'])
    else:
        st.info("No translated content available yet. Complete the translation step to see a preview.")

    # --- Step 3: Convert Translated XML back to EPUB ---
    st.header("Step 3: Create Final EPUB")
    translated_xml_files = list(output_dir.glob("*_translated.xml"))
    if not translated_xml_files:
        st.warning("No translated XML file found. Please complete Step 2.")
    else:
        final_xml = translated_xml_files[0]
        final_epub_path = output_dir / f"{final_xml.stem}.epub"

        st.info(f"Ready to create EPUB from: {final_xml.name}")
        if st.button("Create Translated EPUB", key="create_epub_btn"):
            with st.spinner("Assembling final EPUB file..."):
                try:
                    # This function is already well-structured in xml_to_epub.py
                    create_epub_from_xml(str(final_xml), final_epub_path.name, str(output_dir))
                    st.success(f"Successfully created final EPUB!")

                    # Provide download link
                    with open(final_epub_path, "rb") as f:
                        st.download_button(
                            label="Download Translated EPUB",
                            data=f,
                            file_name=final_epub_path.name,
                            mime="application/epub+zip"
                        )
                except Exception as e:
                    st.error(f"An error occurred during EPUB creation: {e}")

if __name__ == "__main__":
    main()