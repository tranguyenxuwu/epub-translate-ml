import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import requests
import logging
from tqdm import tqdm
from pathlib import Path
import os
import mimetypes
from typing import Dict, List, Tuple
import hashlib
from PIL import Image
import io

class TranslationError(Exception):
    """Custom exception for translation-related errors."""
    pass

class EPUBTranslator:
    def __init__(self, input_path: str, api_url: str, retry_attempts: int = 3):
        self.input_path = Path(input_path)
        self.api_url = api_url
        self.retry_attempts = retry_attempts
        
        self.output_dir = self.input_path.parent / 'translated'
        self.output_dir.mkdir(exist_ok=True)
        self.images_dir = self.output_dir / 'images'
        self.images_dir.mkdir(exist_ok=True)
        
        self.logger = self._setup_logger()
        self.temp_txt_path = self.output_dir / f"{self.input_path.stem}_temp.txt"
        
        self.paragraph_counter = 0
        self.processed_images: Dict[str, Dict[str, str]] = {}

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger('EPUBTranslator')
        logger.setLevel(logging.INFO)
        
        if logger.hasHandlers():
            logger.handlers.clear()
        
        console_handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        log_path = self.output_dir / 'translation.log'
        file_handler = logging.FileHandler(log_path)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        return logger

    def _get_image_dimensions(self, image_content: bytes) -> Tuple[int, int]:
        try:
            with Image.open(io.BytesIO(image_content)) as img:
                return img.size
        except Exception as e:
            self.logger.error(f"Failed to get image dimensions: {e}")
            return (0, 0)

    def _get_image_hash(self, content: bytes) -> str:
        return hashlib.md5(content).hexdigest()

    def _save_image(self, content: bytes, original_path: str) -> Dict[str, str]:
        width, height = self._get_image_dimensions(content)
        
        if width < 100 or height < 100:
            return {'file_path': '', 'dimensions': f"{width}x{height}"}
        
        image_hash = self._get_image_hash(content)
        
        for info in self.processed_images.values():
            if info.get('hash') == image_hash:
                return info
        
        mime_type = mimetypes.guess_type(original_path)[0]
        extension = mimetypes.guess_extension(mime_type) if mime_type else Path(original_path).suffix
        new_filename = f"{image_hash}{extension}"
        image_path = self.images_dir / new_filename
        
        with open(image_path, 'wb') as img_file:
            img_file.write(content)
        
        return {
            'file_path': f"images/{new_filename}",
            'dimensions': f"{width}x{height}",
            'hash': image_hash
        }

    def _translate_text(self, text: str) -> str:
        for attempt in range(self.retry_attempts):
            try:
                resp = requests.post(self.api_url, json={"text": text})
                resp.raise_for_status()
                return resp.json().get('translated_text', text)
            except Exception as e:
                if attempt == self.retry_attempts - 1:
                    self.logger.error(f"Translation failed after {self.retry_attempts} attempts: {e}")
                    raise TranslationError(f"Translation failed: {e}")
        return text





    def _write_formatted_content(self, file, element: BeautifulSoup, content: str, is_image: bool = False):
        """Write content in the specified format based on element type."""
        element_type = element.name
        
        if element_type == 'h1':
            # Reset paragraph counter for new chapter
            self.paragraph_counter = 0
            file.write(f'\n{content}\n\n')
        elif element_type == 'p':
            self.paragraph_counter += 1
            file.write(f'P: {self.paragraph_counter} "{content}"\n')
        elif element_type == 'img' and is_image:
            # Ensure that image file path is written in order
            file.write(f'IMAGE: {content}\n')

    def translate(self) -> None:
        try:
            self.logger.info(f"Starting translation of {self.input_path}")
            book = epub.read_epub(str(self.input_path))

            # Pre-process all images and store their paths
            images = list(book.get_items_of_type(ebooklib.ITEM_IMAGE))
            with tqdm(total=len(images), desc="Processing images") as pbar:
                for image_item in images:
                    try:
                        image_info = self._save_image(
                            image_item.get_content(),
                            image_item.file_name
                        )
                        self.processed_images[image_item.file_name] = image_info
                        pbar.update(1)
                    except Exception as e:
                        self.logger.error(f"Failed to process image {image_item.file_name}: {e}")

            # Process content and replace image src with the processed path
            chapters = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
            with open(self.temp_txt_path, 'w', encoding='utf-8') as temp_txt_file:
                with tqdm(total=len(chapters), desc="Processing content") as pbar:
                    for chapter in chapters:
                        soup = BeautifulSoup(chapter.get_content(), 'html.parser')

                        # Process content while preserving order
                        for element in soup.find_all(['h1', 'img', 'p']):
                            if element.name == 'img':
                                img_src = element.get('src')
                                if img_src in self.processed_images:
                                    image_info = self.processed_images[img_src]
                                    if image_info['file_path']:
                                        # Replace the img src with the path to the saved image
                                        self._write_formatted_content(
                                            temp_txt_file,
                                            element,
                                            image_info['file_path'],
                                            is_image=True
                                        )
                            else:
                                text = element.get_text(strip=True)
                                if text:
                                    try:
                                        translated_text = self._translate_text(text)
                                        self._write_formatted_content(
                                            temp_txt_file,
                                            element,
                                            translated_text
                                        )
                                    except TranslationError:
                                        self._write_formatted_content(
                                            temp_txt_file,
                                            element,
                                            text
                                        )

                        temp_txt_file.flush()
                        pbar.update(1)

            # Log summary
            total_saved = sum(1 for info in self.processed_images.values() if info['file_path'])
            total_skipped = len(self.processed_images) - total_saved
            self.logger.info(f"Translation completed. Output saved to {self.temp_txt_path}")
            self.logger.info(f"Images processed: {len(self.processed_images)}")
            self.logger.info(f"Images saved: {total_saved}")
            self.logger.info(f"Images skipped (too small): {total_skipped}")

        except Exception as e:
            self.logger.error(f"Translation failed: {e}")
            raise





def main():
    translator = EPUBTranslator(
        input_path='./epub/vol-6.epub',
        api_url='https://bedb-34-125-107-49.ngrok-free.app/translate'
    )
    translator.translate()


if __name__ == '__main__':
    main()
