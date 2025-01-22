import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import requests
import logging
from tqdm import tqdm
from pathlib import Path
import os

class TranslationError(Exception):
    """Custom exception for translation-related errors."""
    pass

class EPUBTranslator:
    def __init__(self, input_path: str, api_url: str, retry_attempts: int = 3):
        self.input_path = Path(input_path)
        self.api_url = api_url
        self.retry_attempts = retry_attempts
        self.logger = self._setup_logger()

        # Create output directory next to the input file
        self.output_dir = self.input_path.parent / 'translated'
        self.output_dir.mkdir(exist_ok=True)
        
        # Create images directory
        self.images_dir = self.output_dir / 'images'
        self.images_dir.mkdir(exist_ok=True)

        # Set output path
        self.temp_txt_path = self.output_dir / f"{self.input_path.stem}_temp.txt"

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger('EPUBTranslator')
        logger.setLevel(logging.INFO)
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(console_handler)
        return logger

    def _translate_text(self, text: str) -> str:
        """Translate a single text with retry logic."""
        for attempt in range(self.retry_attempts):
            try:
                self.logger.debug(f"Sending request with text: {text}")
                resp = requests.post(self.api_url, json={"text": text})
                resp.raise_for_status()
                return resp.json().get('translated_text', text)
            except Exception as e:
                if attempt == self.retry_attempts - 1:
                    self.logger.error(f"Translation failed after {self.retry_attempts} attempts: {e}")
                    raise TranslationError(f"Translation failed: {e}")
        return text

    def translate(self) -> None:
        """Main method to translate EPUB and save to TXT."""
        try:
            self.logger.info(f"Processing {self.input_path}")
            book = epub.read_epub(str(self.input_path))

            with open(self.temp_txt_path, 'w', encoding='utf-8') as temp_txt_file:
                for doc in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                    soup = BeautifulSoup(doc.get_content(), 'html.parser')

                    for element in tqdm(soup.find_all(['img', 'title', 'h1', 'h2', 'h3', 'p']), desc="Processing content"):
                        if element.name == 'img':
                            # Handle image
                            img_src = element.get('src')
                            image_item = book.get_item_with_href(img_src)
                            if image_item:
                                try:
                                    # Save image to images directory
                                    image_filename = Path(img_src).name
                                    image_path = self.images_dir / image_filename
                                    with open(image_path, 'wb') as img_file:
                                        img_file.write(image_item.get_content())
                                    
                                    # Write relative image path to TXT
                                    relative_path = f"images/{image_filename}"
                                    temp_txt_file.write(f"IMAGE: {relative_path}\n")
                                    temp_txt_file.flush()
                                    self.logger.info(f"Written image path to TXT: {relative_path}")
                                except Exception as e:
                                    self.logger.warning(f"Failed to process image {img_src}: {e}")
                            else:
                                self.logger.warning(f"Image not found in EPUB: {img_src}. Skipping.")
                        else:
                            # Handle text
                            content_type = element.name
                            text = element.get_text(strip=True)
                            if text:
                                try:
                                    translated_text = self._translate_text(text)
                                    temp_txt_file.write(f"{content_type.upper()}: {translated_text}\n")
                                    temp_txt_file.flush()
                                    # self.logger.info(f"Written translated text to TXT: {content_type.upper()}: {translated_text}")
                                except TranslationError:
                                    self.logger.warning(f"Failed to translate: {text}")
                                    temp_txt_file.write(f"{content_type.upper()}: {text}\n")

            self.logger.info(f"Generated TXT file at {self.temp_txt_path}")

        except Exception as e:
            self.logger.error(f"Translation failed: {e}")
            raise

def main():
    translator = EPUBTranslator(
        input_path='./epub/vol-6.epub', # Path to EPUB file
        api_url='https://cffe-35-247-135-56.ngrok-free.app/translate' # API URL from GoogleCollab (add /translate endpoint)
    )
    translator.translate()

if __name__ == '__main__':
    main()