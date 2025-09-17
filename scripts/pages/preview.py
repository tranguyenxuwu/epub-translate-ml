import streamlit as st
import sys
from pathlib import Path

# Set page config
st.set_page_config(
    page_title="Translation Preview", 
    page_icon="📖", 
    layout="wide",
    initial_sidebar_state="expanded"
)

def main():
    st.title("📖 Translation Preview")
    st.markdown("---")
    
    # Get the output directory path
    output_dir = Path("output")
    
    if not output_dir.exists():
        st.error("Output directory not found. Please run the main translation workflow first.")
        if st.button("← Back to Main Workflow"):
            st.switch_page("app.py")
        return
    
    # Try to import and run the preview functionality
    try:
        # Add parent directory to path to import st_preview_content
        parent_dir = Path(__file__).parent.parent
        if str(parent_dir) not in sys.path:
            sys.path.insert(0, str(parent_dir))
        
        # Import and run the preview content with arguments
        import st_preview_content
        st_preview_content.run_preview(
            output_dir_path=str(output_dir),
            title=None,  # Don't show title since we already have one
            show_navigation=True
        )
        
    except ImportError as e:
        st.error(f"Could not import preview module: {e}")
        show_fallback_preview(output_dir)
    except Exception as e:
        st.error(f"Error running preview: {e}")
        show_fallback_preview(output_dir)

def show_fallback_preview(output_dir):
    """Show a basic fallback preview when the main preview module fails"""
    st.warning("Using fallback preview mode. For full preview functionality, ensure `st_preview_content.py` is available.")
    
    # Show basic file information
    st.subheader("📁 Available Files")
    
    progress_files = list(output_dir.glob("*progress.json"))
    xml_files = list(output_dir.glob("*.xml"))
    translated_xml_files = list(output_dir.glob("*_translated.xml"))
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.write("**Progress Files:**")
        if progress_files:
            for file in progress_files:
                st.write(f"- {file.name}")
                if st.button(f"View {file.name}", key=f"view_{file.name}"):
                    try:
                        import json
                        with open(file, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        st.json(data)
                    except Exception as e:
                        st.error(f"Error reading file: {e}")
        else:
            st.write("No progress files found")
    
    with col2:
        st.write("**XML Files:**")
        if xml_files:
            for file in xml_files:
                st.write(f"- {file.name}")
        else:
            st.write("No XML files found")
    
    with col3:
        st.write("**Translated XML Files:**")
        if translated_xml_files:
            for file in translated_xml_files:
                st.write(f"- {file.name}")
        else:
            st.write("No translated XML files found")
    
    # Instructions for manual preview
    st.markdown("""
    ### 📋 Manual Preview Instructions
    
    To access the full preview functionality, run the following command in your terminal:
    
    ```bash
    streamlit run st_preview_content.py --server.port 8502
    ```
    
    Then visit: http://localhost:8502
    """)

if __name__ == "__main__":
    main()
