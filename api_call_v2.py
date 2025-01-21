import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, NavigableString
import os
from typing import List, Dict, Optional, Union
import re
import logging
import requests
from dataclasses import dataclass
from tqdm import tqdm
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('translation.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

@dataclass
class Chapter:
    """Store chapter information with metadata."""
    title: str
    content: str
    order: int
    file_name: str
    word_count: int = 0
    translation_status: bool = False


class TextProcessor:
    """Handle text processing and formatting."""
    @staticmethod
    def clean_text(text: str) -> str:
        text = re.sub(r'[\s\u200b\u3000]+', ' ', text)
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        return text.strip()

    @staticmethod
    def format_dialogue(text: str) -> str:
        text = re.sub(r'([。．！？」』]\s*)', r'\1\n', text)
        text = re.sub(r'\s*([「『])', r'\n\1', text)
        return text

    @staticmethod
    def format_sentences(text: str) -> str:
        text = re.sub(r'([.!?]\s+)([A-Z])', r'\1\n\2', text)
        return text


class EpubTranslator:
    def __init__(
        self,
        input_path: str,
        server_url: str,
        chunk_size: int = 1000,
        timeout: int = 30
    ):
        self.input_path = Path(input_path)
        self.server_url = server_url.rstrip('/')
        self.chunk_size = chunk_size
        self.timeout = timeout
        self.chapters: List[Chapter] = []
        self.book = epub.EpubBook()
        self.text_processor = TextProcessor()

    def read_epub(self):
        logging.info(f"Reading EPUB file: {self.input_path}")
        try:
            book = epub.read_epub(str(self.input_path))
            self.book.set_identifier(book.get_metadata('DC', 'identifier')[0][0] if book.get_metadata('DC', 'identifier') else str(self.input_path))
            self.book.set_title(book.get_metadata('DC', 'title')[0][0] if book.get_metadata('DC', 'title') else "Translated Book")
            self.book.set_language('vi')

            if book.get_metadata('DC', 'creator'):
                self.book.add_author(book.get_metadata('DC', 'creator')[0][0])

            for order, item in enumerate(book.get_items_of_type(ebooklib.ITEM_DOCUMENT)):
                content = item.get_content().decode('utf-8')
                text = self.text_processor.clean_text(content)
                self.chapters.append(Chapter(
                    title=f"Chapter {order + 1}",
                    content=text,
                    order=order,
                    file_name=item.get_name(),
                    word_count=len(text.split())
                ))
            logging.info(f"Read {len(self.chapters)} chapters successfully.")
        except Exception as e:
            logging.error(f"Error reading EPUB: {e}")
            raise

    def translate_text(self, text: str) -> str:
        if not text.strip():
            return text
        try:
            response = requests.post(
                self.server_url,
                json={"text": text},
                headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
                timeout=self.timeout
            )
            response.raise_for_status()
            result = response.json()
            translated_text = result.get('translated_text', text)
            translated_text = self.text_processor.clean_text(translated_text)
            return translated_text
        except Exception as e:
            logging.error(f"Translation error: {e}")
            return text

    def translate_chapters(self, output_file: str):
        logging.info("Starting translation process.")
        for chapter in tqdm(self.chapters, desc="Translating chapters"):
            chunks = [chapter.content[i:i + self.chunk_size] for i in range(0, len(chapter.content), self.chunk_size)]
            translated_chunks = [self.translate_text(chunk) for chunk in chunks]
            chapter.content = '\n'.join(translated_chunks)
            chapter.translation_status = True

        epub.write_epub(output_file, self.book)
        logging.info("Translation process completed.")

    def save_translations_to_txt(self, output_txt_file: str):
        logging.info(f"Saving translations to {output_txt_file}.")
        with open(output_txt_file, 'w', encoding='utf-8') as f:
            for chapter in self.chapters:
                f.write(f"\n\n--- {chapter.title} ---\n\n")
                f.write(chapter.content)
        logging.info("Translations saved successfully.")


def main():
    config = {
        'input_file': 'vol-5.epub',
        'output_file': 'translated.epub',
        'txt_output_file': 'translated.txt',
        'server_url': 'https://b6d6-34-141-242-248.ngrok-free.app/translate',
        'chunk_size': 300,
        'timeout': 30
    }
    translator = EpubTranslator(
        config['input_file'],
        config['server_url'],
        chunk_size=config['chunk_size'],
        timeout=config['timeout']
    )
    try:
        translator.read_epub()
        translator.translate_chapters(config['output_file'])
        translator.save_translations_to_txt(config['txt_output_file'])
    except Exception as e:
        logging.error(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
