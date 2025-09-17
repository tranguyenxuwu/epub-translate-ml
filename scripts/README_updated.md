# EPUB Translator - Updated

A Streamlit-based web application for translating EPUB files using AI translation services.

## New Features ✨

### 1. **Improved Navigation**

- **Main Workflow**: Complete EPUB translation pipeline
- **Translation Preview**: Dedicated preview page for viewing translated content
- Use the sidebar to navigate between pages

### 2. **Enhanced Translation Control**

- **Stop Translation**: Click the "🛑 Stop" button to stop translation after the current batch
- **Auto-Resume**: Translation automatically resumes from where it left off
- **Progress Tracking**: Real-time progress display with completion percentage

### 3. **Previous Translation Detection**

- Automatically detects previous translation progress
- Shows completion status and translated element count
- Seamless resume functionality

### 4. **Separated Preview**

- Preview functionality moved to dedicated page
- Better performance and organization
- Access via sidebar navigation

## How to Run

### Option 1: Use the Launcher (Recommended)

```bash
python run_app.py
```

### Option 2: Direct Streamlit

```bash
streamlit run app.py
```

### Option 3: Preview Only

```bash
streamlit run st_preview_content.py --server.port 8502
```

## Usage Guide

### 1. Main Workflow

1. **Upload EPUB**: Choose your EPUB file and convert to XML
2. **Configure Translation**:
   - Set API key (required)
   - Choose translation model
   - Adjust batch size
   - Customize translation prompt
3. **Translate**:
   - Click "Translate XML" to start
   - Monitor progress with the progress bar
   - Use "🛑 Stop" to pause translation if needed
   - Resume automatically by clicking "Translate XML" again
4. **Create EPUB**: Generate the final translated EPUB file

### 2. Translation Preview

- Use sidebar navigation to switch to "📖 Translation Preview"
- View translated content chapter by chapter
- Navigate between chapters
- See translation statistics

## Key Improvements

- ✅ **Stop/Resume Translation**: You can now stop and resume translations
- ✅ **Progress Bar**: Real-time translation progress tracking
- ✅ **Auto-Detection**: Automatically detects and resumes previous translations
- ✅ **Organized UI**: Separated main workflow and preview into different pages
- ✅ **Better Error Handling**: Improved error messages and recovery
- ✅ **Performance**: Better memory usage and responsiveness

## Requirements

- Python 3.8+
- streamlit
- openai
- python-dotenv
- tiktoken
- Pillow
- All other dependencies from requirements.txt

## Environment Variables

Set your API key:

```bash
# In .env file or environment
API_KEY=your_api_key_here
```

## Supported Models

- microsoft/mai-ds-r1:free (default)
- anthropic/claude-3.5-sonnet
- openai/gpt-4o
- openai/gpt-4o-mini
- google/gemini-pro-1.5
- meta-llama/llama-3.1-70b-instruct
- Custom models (enter manually)

## File Structure

```
├── app.py                    # Main Streamlit application
├── st_preview_content.py     # Preview functionality
├── epub_to_xml.py           # EPUB to XML conversion
├── translate_xml.py         # XML translation with AI
├── xml_to_epub.py          # XML to EPUB conversion
├── run_app.py              # Launcher script
├── pages/
│   └── preview.py          # Preview page (fallback)
└── output/                 # Generated files directory
```

## Troubleshooting

1. **Translation Not Resuming**: Check that the progress JSON file exists in the output directory
2. **Preview Not Working**: Use the direct preview command: `streamlit run st_preview_content.py`
3. **Stop Button Not Working**: The translation will stop after the current batch completes
4. **API Errors**: Check your API key and model selection

## Usage Tips

- **Large Files**: Use smaller batch sizes (10-30) for better reliability
- **Resume**: Always check the progress indicator before starting translation
- **Stop Safely**: Use the stop button rather than closing the browser
- **Preview**: Check the preview page periodically to verify translation quality
