import asyncio
import aiohttp
from ebooklib import epub
import logging
from bs4 import BeautifulSoup
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
import re
import html
from tqdm.asyncio import tqdm_asyncio

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

@dataclass
class BookConfig:
    input_path: Path
    output_path: Path
    server_url: str
    chunk_size: int = 1000
    concurrent_requests: int = 5
    retry_attempts: int = 3
    timeout: int = 30
    language: str = 'en'

@dataclass
class Chapter:
    id: str
    title: str
    content: str
    order: int
    file_name: str
    translated_content: Optional[str] = None

class AsyncEpubTranslator:
    def __init__(self, config: BookConfig):
        self.config = config
        self.chapters: List[Chapter] = []
        self.session: Optional[aiohttp.ClientSession] = None
        self.semaphore = asyncio.Semaphore(config.concurrent_requests)
        self.book_metadata = {}

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.config.timeout),
            headers={'Content-Type': 'application/json; charset=utf-8'}
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    def load_book(self) -> None:
        book = epub.read_epub(self.config.input_path)

        self.book_metadata = {
            'identifier': book.get_metadata('DC', 'identifier')[0][0] if book.get_metadata('DC', 'identifier') else 'unknown',
            'title': book.get_metadata('DC', 'title')[0][0] if book.get_metadata('DC', 'title') else 'Untitled',
            'language': book.get_metadata('DC', 'language')[0][0] if book.get_metadata('DC', 'language') else self.config.language
        }

        # Use 'application/xhtml+xml' instead of ITEM_DOCUMENT
        items = [item for item in book.get_items() if item.get_type() == 'application/xhtml+xml']
        spine_order = {itemref: i for i, itemref in enumerate(book.spine)}
        items.sort(key=lambda x: spine_order.get(x.id, float('inf')))

        for order, item in enumerate(items):
            content = item.get_content().decode('utf-8')
            text = self._extract_text(content)
            self.chapters.append(Chapter(
                id=item.id,
                title=f"Chapter {order + 1}",
                content=text,
                order=order,
                file_name=item.get_name()
            ))

    def _extract_text(self, html_content: str) -> str:
        soup = BeautifulSoup(html_content, 'html.parser')
        for tag in soup(['script', 'style', 'img']):
            tag.decompose()
        return soup.get_text(separator='\n').strip()

    def _split_text(self, text: str) -> List[str]:
        chunks = []
        current_chunk = ""
        sentences = re.split(r'([.!?])', text)

        for i in range(0, len(sentences), 2):
            sentence = sentences[i]
            if i + 1 < len(sentences):
                sentence += sentences[i + 1]

            if len(current_chunk) + len(sentence) <= self.config.chunk_size:
                current_chunk += sentence
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sentence

        if current_chunk:
            chunks.append(current_chunk.strip())

        return chunks

    async def _translate_chunk(self, text: str) -> str:
        if not text.strip():
            return text

        text = html.unescape(text)
        async with self.semaphore:
            for attempt in range(self.config.retry_attempts):
                try:
                    async with self.session.post(
                        f"{self.config.server_url.rstrip('/')}/translate",
                        json={"text": text}
                    ) as response:
                        response.raise_for_status()
                        result = await response.json()
                        return result.get('translated_text', text)
                except Exception as e:
                    if attempt == self.config.retry_attempts - 1:
                        logging.error(f"Failed to translate chunk: {e}")
                        return text
                    await asyncio.sleep(2 ** attempt)

    async def translate_chapter(self, chapter: Chapter) -> Chapter:
        chunks = self._split_text(chapter.content)
        translated_chunks = await asyncio.gather(*[self._translate_chunk(chunk) for chunk in chunks])
        chapter.translated_content = '\n'.join(translated_chunks)
        return chapter

    def create_epub(self) -> epub.EpubBook:
        book = epub.EpubBook()
        book.set_identifier(self.book_metadata['identifier'])
        book.set_title(f"{self.book_metadata['title']} (Translated)")
        book.set_language(self.book_metadata['language'])

        style = '''
        @font-face {
            font-family: 'Roboto';
            src: url('Roboto-Regular.ttf');
        }
        body { font-family: 'Roboto', sans-serif; }
        '''
        css = epub.EpubItem(uid='style', file_name='style.css', content=style, media_type='text/css')
        book.add_item(css)

        return book

    async def process(self) -> None:
        self.load_book()
        book = self.create_epub()

        translated_chapters = await tqdm_asyncio.gather(
            *[self.translate_chapter(chapter) for chapter in self.chapters],
            desc="Translating chapters",
            total=len(self.chapters)
        )

        for chapter in translated_chapters:
            html_content = f'''<?xml version="1.0" encoding="utf-8"?>
            <!DOCTYPE html>
            <html xmlns="http://www.w3.org/1999/xhtml">
            <head><link rel="stylesheet" type="text/css" href="style.css"/></head>
            <body>{chapter.translated_content}</body>
            </html>'''

            epub_chapter = epub.EpubHtml(
                title=chapter.title,
                file_name=chapter.file_name,
                content=html_content
            )
            book.add_item(epub_chapter)

        book.spine = ['nav'] + [chapter.file_name for chapter in self.chapters]
        book.toc = [epub.Link(chapter.file_name, chapter.title, chapter.id) for chapter in self.chapters]
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())

        epub.write_epub(self.config.output_path, book)
        logging.info(f"Translated EPUB saved to: {self.config.output_path}")

async def main():
    config = BookConfig(
        input_path=Path("vol-5.epub"),
        output_path=Path("translated_output.epub"),
        server_url="https://da2a-35-197-23-79.ngrok-free.app/"
    )

    async with AsyncEpubTranslator(config) as translator:
        await translator.process()

if __name__ == "__main__":
    asyncio.run(main())
