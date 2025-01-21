import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, NavigableString, Tag
import os
from typing import List
import re
import logging
import requests
from dataclasses import dataclass
from tqdm import tqdm

# Cấu hình logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

@dataclass
class Chapter:
    """Lưu trữ thông tin của một chương"""
    title: str
    content: str
    order: int
    file_name: str

class EpubTranslator:
    def __init__(self, input_path: str, server_url: str, chunk_size: int = 1000):
        self.input_path = input_path
        self.server_url = server_url
        self.chunk_size = chunk_size
        self.chapters: List[Chapter] = []
        self.book = epub.EpubBook()  # Initialize the new book for translated chapters

    def extract_text_from_html(self, html_content: str) -> str:
        """Trích xuất text từ HTML và loại bỏ ảnh, giữ lại định dạng cơ bản"""
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Xóa tất cả thẻ img
        for img in soup.find_all('img'):
            img.decompose()
        
        # Loại bỏ các thẻ script và style
        for tag in soup(['script', 'style']):
            tag.decompose()
        
        # Giữ lại các thẻ định dạng cơ bản
        allowed_tags = ['p', 'br', 'strong', 'em', 'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']
        for tag in soup.find_all(True):
            if tag.name not in allowed_tags:
                tag.unwrap()
        
        text = soup.get_text(separator='\n')
        return text.strip()

    def split_text(self, text: str) -> List[str]:
        """Chia text thành các chunk nhỏ hơn"""
        chunks = []
        current_chunk = ""
        
        sentences = re.split(r'([.!?।])', text)
        
        for i in range(0, len(sentences), 2):
            sentence = sentences[i]
            if i + 1 < len(sentences):
                sentence += sentences[i + 1]
                
            if len(current_chunk) + len(sentence) <= self.chunk_size:
                current_chunk += sentence
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sentence
                
        if current_chunk:
            chunks.append(current_chunk.strip())
            
        return chunks

    def read_epub(self):
        """Đọc và parse file epub"""
        logging.info(f"Đọc file epub: {self.input_path}")
        book = epub.read_epub(self.input_path)
        
        items = [item for item in book.get_items() if item.get_type() == ebooklib.ITEM_DOCUMENT]
        
        spine_order = {itemref: i for i, itemref in enumerate(book.spine)}
        items.sort(key=lambda x: spine_order.get(x.id, float('inf')))
        
        for order, item in enumerate(items):
            content = item.get_content().decode('utf-8')
            text = self.extract_text_from_html(content)
            
            chapter = Chapter(
                title=f"Chapter {order + 1}",
                content=text,
                order=order,
                file_name=item.get_name()
            )
            self.chapters.append(chapter)
            
        logging.info(f"Đã đọc xong {len(self.chapters)} chương")

    def translate_text(self, text: str) -> str:
        """Gửi yêu cầu dịch văn bản đến server"""
        try:
            url = self.server_url.rstrip('/') + '/translate'
            
            payload = {
                "text": text
            }
            headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            
            total_words = len(text.split())
            words_sent = sum(len(t.split()) for t in payload.values())
            percentage = (words_sent / total_words * 100) if total_words > 0 else 0
            logging.debug(f"Sending {words_sent} words out of {total_words} total words ({percentage:.1f}%)")
            
            response = requests.post(
                url,
                json=payload,
                headers=headers
            )
            
            response.raise_for_status()
            
            result = response.json()
            return result.get('translated_text', text)
            
        except requests.RequestException as e:
            logging.warning(f"Translation failed: {str(e)}")
            return text

    def is_chapter_empty(self, content: str) -> bool:
        """Kiểm tra xem chương có nội dung hay không và điều chỉnh thứ tự chương"""
        is_empty = len(content.strip()) == 0
        if is_empty:
            # Find current chapter number from title
            current_chapter = next((ch for ch in self.chapters if ch.content == content), None)
            if current_chapter:
                current_idx = self.chapters.index(current_chapter)
                # Update titles of all subsequent chapters
                for i in range(current_idx + 1, len(self.chapters)):
                    old_num = i + 1
                    new_num = i
                    self.chapters[i].title = f"Chapter {new_num}"
                    logging.info(f"Skipped empty Chapter {old_num-1}, Chapter {old_num} is now Chapter {new_num}")
        return is_empty

    def write_to_txt(self, txt_output_path: str, chapter_title: str, content: str):
        """Ghi nội dung chương vào file TXT"""
        with open(txt_output_path, 'a', encoding='utf-8') as f:
            f.write(f"--- {chapter_title} ---\n")
            f.write(content + "\n\n")
            logging.info(f"Đã ghi chương {chapter_title} vào file TXT")

    def translate_chapters(self, output_path: str, txt_output_path: str):
        """Dịch tất cả các chương và viết vào EPUB ngay lập tức"""
        logging.info("Bắt đầu quá trình dịch")
        
        original_book = epub.read_epub(self.input_path)
        identifier = original_book.get_metadata('DC', 'identifier')
        self.book.set_identifier(identifier if identifier else "default-identifier")
        
        title = original_book.get_metadata('DC', 'title') or "Default Book Title"
        logging.info(f"Title from metadata: {title}")
        
        if isinstance(title, tuple):
            title = ''.join(str(t) for t in title)
        elif isinstance(title, list):
            title = ''.join(str(t) for t in title)
        elif not isinstance(title, str):
            title = "Default Book Title"
        
        self.book.set_title(title + " (Translated)" if title else "Translated Book")
        self.book.set_language('vi')

        chapters_epub = []
        sections = []
        
        for chapter in tqdm(self.chapters, desc="Đang dịch các chương"):
            if self.is_chapter_empty(chapter.content):
                logging.info(f"Bỏ qua chương '{chapter.title}' vì nó không có nội dung.")
                continue
            
            chunks = self.split_text(chapter.content)
            translated_chunks = []
            
            for chunk in tqdm(chunks, desc=f"Đang dịch {chapter.title}", leave=False):
                if not chunk.strip():
                    continue
                
                translated_chunk = self.translate_text(chunk)
                translated_chunks.append(translated_chunk)
            
            chapter.content = '\n'.join(translated_chunks)
            
            # Tạo và thêm chương vào EPUB
            epub_chapter = epub.EpubHtml(
                title=chapter.title,
                file_name=chapter.file_name,
                content=chapter.content
            )
            self.book.add_item(epub_chapter)
            chapters_epub.append(epub_chapter)
            
            # Tạo phần trong mục lục
            sections.append(epub.Section(chapter.title, [epub_chapter]))
            
            # Ghi vào file txt sau mỗi chương được dịch
            self.write_to_txt(txt_output_path, chapter.title, chapter.content)

        # Set spine và toc sau khi đã xử lý tất cả các chương
        self.book.spine = [item for item in chapters_epub]
        self.book.toc = sections

        # Thêm các mục cần thiết vào EPUB
        self.book.add_item(epub.EpubNcx())
        self.book.add_item(epub.EpubNav())
        
        # Ghi file EPUB đã dịch
        epub.write_epub(output_path, self.book)
        logging.info(f"Đã lưu file EPUB đã dịch: {output_path}")

def main():
    input_file = "vol-5.epub"
    output_file = "output_translated.epub"
    txt_output_file = "vol-5.txt"
    # Create empty txt file if not found
    if not os.path.exists(txt_output_file):
        with open(txt_output_file, 'w', encoding='utf-8') as f:
            pass
    server_url = "https://211a-34-34-71-205.ngrok-free.app/"
    
    translator = EpubTranslator(input_file, server_url)
    
    try:
        translator.read_epub()
        translator.translate_chapters(output_file, txt_output_file)
        
        logging.info("Hoàn thành quá trình dịch!")
        
    except Exception as e:
        logging.error(f"Có lỗi xảy ra: {str(e)}")
        raise

if __name__ == "__main__":
    main()
