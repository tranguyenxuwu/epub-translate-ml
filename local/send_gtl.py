import time
import logging
import asyncio
from typing import List
from googletrans import Translator, LANGUAGES
from contextlib import contextmanager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Constants
MAX_SERVER_ERRORS = 3
BATCH_SIZE = 8

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

async def translate_text(text: str, translator: Translator) -> str:
    """
    Translate a single text string using Google Translate API.
    """
    try:
        translation = await translator.translate(text, dest='vi')
        return translation.text
    except Exception as e:
        if "5" in str(e):  # Check if it's a 5xx server error
            logging.error("❌ Too many server errors, stopping program.")
            exit(1)
        logging.error(f"⚠️ Translation failed: {str(e)}")
        return text  # Return original text if translation fails

async def translate_texts(texts: List[str], translator: Translator) -> List[str]:
    """
    Translate a list of text strings using Google Translate API.
    """
    tasks = [translate_text(text, translator) for text in texts]
    return await asyncio.gather(*tasks)

async def process_file(input_file: str, output_file: str):
    """
    Read TXT file and translate lines starting with 'P: ', writing results immediately in batches.
    """
    try:
        translator = Translator()
        
        with safe_open(input_file, 'r') as in_file, safe_open(output_file, 'w') as out_file:
            batch = []
            batch_lines = []
            
            for line in in_file:
                if line.startswith("P: ") and line.strip() != "P:":
                    text_to_translate = line[3:].strip()
                    batch.append(text_to_translate)
                    batch_lines.append(line)
                    
                    if len(batch) == BATCH_SIZE:
                        translated_batch = await translate_texts(batch, translator)
                        for original, translated in zip(batch_lines, translated_batch):
                            out_file.write(f"P: {translated}\n")
                        out_file.flush()
                        batch.clear()
                        batch_lines.clear()
                else:
                    out_file.write(line)
                    out_file.flush()
            
            # Process remaining batch
            if batch:
                translated_batch = await translate_texts(batch, translator)
                for original, translated in zip(batch_lines, translated_batch):
                    out_file.write(f"P: {translated}\n")
                out_file.flush()
        
        logging.info(f"🎉 Translation complete! Results saved to: {output_file}")
        
    except FileNotFoundError:
        logging.error(f"❌ Input file not found: {input_file}")
    except PermissionError:
        logging.error("❌ Permission denied when accessing files")
    except Exception as e:
        logging.error(f"❌ Unexpected error: {str(e)}")

if __name__ == "__main__":
    asyncio.run(process_file("./output_v2/content.txt", "./output_v2/tl.txt"))