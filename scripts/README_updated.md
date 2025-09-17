# EPUB Translator - Desktop Edition

A Tkinter-based desktop application for translating EPUB files using AI translation services.

## Highlights ✨

### Streamlined desktop workflow

- **Guided steps** for converting EPUB → XML → translated EPUB
- **Integrated preview tab** with chapter/paragraph navigation and inline image support
- **Persistent progress tracking** that automatically resumes unfinished translations

### Flexible translation controls

- Configure API key, model, batch size, and custom prompts
- Quick templates for light-novel, general, and formal translation styles
- Real-time progress indicators with the ability to stop gracefully after the current batch

### Smarter media handling

- The first extracted image is automatically assigned to the `<cover>` tag
- Preview tab displays images next to text content for quick verification

## How to Run

### Option 1 · Recommended launcher

```bash
python run_app.py
```

### Option 2 · Direct execution

```bash
python app.py
```

## Usage Guide

### 1. Main workflow tab

1. **Select EPUB**: Choose a source file and convert it to XML
2. **Configure translation**:
   - Enter your API key (required)
   - Select a model or provide a custom endpoint name
   - Adjust batch size and customise the translation prompt
3. **Start translation**:
   - Monitor progress via the built-in progress bar and activity log
   - Use the **Stop** button to pause after the current batch – progress is saved
4. **Create EPUB**: When translation is complete, generate the final EPUB with one click

### 2. Preview tab

- Load the latest XML/progress data with one button
- Browse chapters, paragraphs, and images in a tree view
- Inspect original vs. translated text side-by-side
- View embedded images directly inside the application

## Key Improvements

- ✅ Fully desktop-native UI (no browser required)
- ✅ Automatic cover selection from the first extracted image
- ✅ Integrated preview experience with text and image support
- ✅ Enhanced logging and feedback through the activity log
- ✅ Simplified launch workflow (`python run_app.py`)

## Requirements

- Python 3.8+
- openai
- python-dotenv
- tiktoken
- ebooklib
- beautifulsoup4
- pillow

> **Note:** Tkinter ships with standard Python distributions on Windows, macOS, and most Linux builds. No extra installation is required.

## Environment Variables

Set your API key in an environment variable or `.env` file:

```bash
API_KEY=your_api_key_here
```

## File Structure

```
├── app.py              # Tkinter UI for the translation workflow
├── epub_to_xml.py      # EPUB to XML conversion pipeline
├── translate_xml.py    # AI-powered translation logic
├── xml_to_epub.py      # XML back to EPUB conversion
├── run_app.py          # Convenience launcher for the desktop UI
└── output/             # Generated XML, progress JSON, images, and EPUB files
```

## Troubleshooting

1. **No translation progress detected** → Ensure `output/*.xml` exists and that the translation step has been run at least once.
2. **API errors** → Confirm the API key and model name are valid for your provider.
3. **Images not loading in preview** → Verify that the `output/images` directory contains extracted assets.
4. **UI not starting** → Ensure you are running a Python build with Tkinter support (`python -m tkinter` should open a test window).

## Usage Tips

- Use smaller batch sizes (10–30) for unstable network conditions.
- Keep the application open after pressing **Stop** so the current batch can finish cleanly.
- Refresh the preview tab after long translation runs to see the latest content.
