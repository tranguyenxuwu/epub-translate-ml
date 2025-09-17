#!/usr/bin/env python3
"""
Launcher script for the EPUB Translator Streamlit app.
This script ensures the app runs with the correct configuration.
"""

import subprocess
import sys
import os
from pathlib import Path

def main():
    # Change to the script directory
    script_dir = Path(__file__).parent
    os.chdir(script_dir)
    
    # Check if required files exist
    required_files = [
        "app.py",
        "epub_to_xml.py", 
        "translate_xml.py",
        "xml_to_epub.py",
        "st_preview_content.py"
    ]
    
    missing_files = [f for f in required_files if not Path(f).exists()]
    if missing_files:
        print(f"❌ Missing required files: {', '.join(missing_files)}")
        return 1
    
    print("🚀 Starting EPUB Translator...")
    print(f"📁 Working directory: {script_dir}")
    print("🌐 The app will open in your default browser")
    print("⚠️  Press Ctrl+C to stop the app")
    print("-" * 50)
    
    try:
        # Run streamlit app
        cmd = [sys.executable, "-m", "streamlit", "run", "app.py", "--server.headless", "false"]
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        print("\n👋 App stopped by user")
    except subprocess.CalledProcessError as e:
        print(f"❌ Error running app: {e}")
        return 1
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
