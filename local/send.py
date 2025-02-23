import requests
import json
import time
import logging
from typing import List, Dict, Optional, Union, TextIO
from contextlib import contextmanager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# API configuration
API_BASE_URL = "https://56fc-34-82-190-225.ngrok-free.app/"

class TranslationClient:
    def __init__(self, base_url: str = API_BASE_URL):
        """
        Initialize client with API base URL.
        Args:
            base_url (str): Base URL for the translation API
        """
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.check_connection()

    def check_connection(self) -> bool:
        """
        Check server connection.
        Returns:
            bool: True if connection is successful, False otherwise
        """
        try:
            response = self.session.get(f"{self.base_url}/health", timeout=10)
            response.raise_for_status()
            logging.info("✅ Successfully connected to translation server!")
            return True
        except requests.exceptions.RequestException as e:
            logging.error(f"❌ Cannot connect to server: {str(e)}")
            return False
            
    def translate_text(self, text: str) -> Optional[str]:
        """
        Translate a single text segment.
        Args:
            text (str): Text to translate
        Returns:
            Optional[str]: Translated text or None if translation failed
        """
        if not text or not isinstance(text, str):
            logging.error("❌ Invalid input data.")
            return None
            
        endpoint = f"{self.base_url}/translate"

        try:
            logging.info(f"🔄 Sending translation request: {text[:50]}...")
            response = self.session.post(
                endpoint,
                json={"text": text},
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            response.raise_for_status()
            result = response.json()
            
            # Handle both string and dict response formats
            if isinstance(result, dict):
                return result.get("translated_text", "").strip()
            elif isinstance(result, str):
                return result.strip()
            else:
                logging.error(f"❌ Unexpected response format: {type(result)}")
                return None
                
        except requests.exceptions.Timeout:
            logging.error("⏳ Request timed out!")
        except requests.exceptions.RequestException as e:
            logging.error(f"⚠️ Error during request: {str(e)}")
        except json.JSONDecodeError as e:
            logging.error(f"❌ Invalid JSON response: {str(e)}")
        return None

@contextmanager
def safe_open(filename: str, mode: str = 'r', encoding: str = 'utf-8'):
    """
    Safely open a file with proper error handling.
    """
    file = None
    try:
        file = open(filename, mode, encoding=encoding)
        yield file
    except IOError as e:
        logging.error(f"❌ Error accessing file {filename}: {str(e)}")
        raise
    finally:
        if file:
            file.close()

def process_file(input_file: str, output_file: str, max_retries: int = 3, retry_delay: float = 2.0):
    """
    Read TXT file and translate lines starting with 'P: ', writing results immediately.
    Args:
        input_file (str): Path to input file
        output_file (str): Path to output file
        max_retries (int): Maximum number of retry attempts
        retry_delay (float): Delay between retries in seconds
    """
    try:
        translator = TranslationClient()
        
        # First count total lines needing translation
        with safe_open(input_file, 'r') as f:
            total_lines = sum(1 for line in f if line.startswith("P: ") and line.strip() != "P:")
        
        logging.info(f"📄 Found {total_lines} lines that need translation")
        
        # Process file line by line
        translated_count = 0
        
        with safe_open(input_file, 'r') as in_file, safe_open(output_file, 'w') as out_file:
            for line in in_file:
                if line.startswith("P: ") and line.strip() != "P:":
                    text_to_translate = line[3:].strip()
                    translated_text = None
                    
                    # Try translation with retries
                    for attempt in range(max_retries):
                        translated_text = translator.translate_text(text_to_translate)
                        if translated_text:
                            break
                        elif attempt < max_retries - 1:
                            logging.warning(f"🔁 Retrying translation ({attempt + 1}/{max_retries}) in {retry_delay} seconds...")
                            time.sleep(retry_delay)
                    
                    # Write result immediately
                    if translated_text:
                        out_file.write(f"P: {translated_text}\n")
                        out_file.flush()  # Force write to disk
                    else:
                        logging.warning(f"⚠️ Translation failed, keeping original text: {text_to_translate[:50]}...")
                        out_file.write(line)  # Keep original line if translation failed
                        out_file.flush()
                    
                    translated_count += 1
                    logging.info(f"✅ Progress: {translated_count}/{total_lines} lines")
                else:
                    out_file.write(line)
                    out_file.flush()
        
        logging.info(f"🎉 Translation complete! Results saved to: {output_file}")
        
    except FileNotFoundError:
        logging.error(f"❌ Input file not found: {input_file}")
    except PermissionError:
        logging.error(f"❌ Permission denied when accessing files")
    except Exception as e:
        logging.error(f"❌ Unexpected error: {str(e)}")

if __name__ == "__main__":
    process_file("input2.txt", "output.txt")