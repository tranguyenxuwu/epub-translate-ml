from ebooklib import epub
import os
import re

def create_epub(txt_file, output_file, image_folder):
    with open(txt_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    title, author, language = None, None, 'vi'
    images = {img: os.path.join(image_folder, img) for img in os.listdir(image_folder) if img.lower().endswith(('jpg', 'jpeg', 'png'))}
    content = []
    
    for line in lines:
        line = line.strip()
        if line.startswith("Title:"):
            title = line.split("Title:")[1].strip()
        elif line.startswith("Author:"):
            author = line.split("Author:")[1].strip()
        elif line.startswith("Language:"):
            language = line.split("Language:")[1].strip()
        elif line.startswith("IMAGE:"):
            img_name = line.split("IMAGE:")[1].strip()
            if img_name in images:
                content.append(f'<img src="images/{img_name}" alt="{img_name}"/>')
        elif not line.startswith("CH:") and not line.startswith("P:"):
            content.append(line)
    
    if not title:
        title = "Untitled"
    if not author:
        author = "Unknown"
    
    book = epub.EpubBook()
    book.set_title(title)
    book.set_language(language)
    book.add_author(author)
    
    if images:
        cover_img = next(iter(images.values()))
        with open(cover_img, 'rb') as img_file:
            book.set_cover(os.path.basename(cover_img), img_file.read())
    
    chapters = []
    chapter_text = ""
    chapter_title = "Nội dung"
    
    for line in content:
        if re.match(r'^\s*$', line):
            continue
        chapter_text += f'{line}<br/>'
    
    if chapter_text:
        chapter = epub.EpubHtml(title=chapter_title, file_name=f'chapter.xhtml')
        chapter.set_content(f'<h2>{chapter_title}</h2><p>{chapter_text}</p>')
        book.add_item(chapter)
        chapters.append(chapter)
    
    for img_name, img_path in images.items():
        with open(img_path, 'rb') as img_file:
            img_item = epub.EpubItem(uid=img_name, file_name=f'images/{img_name}', media_type='image/jpeg', content=img_file.read())
            book.add_item(img_item)
    
    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ['nav'] + chapters
    
    epub.write_epub(output_file, book, {})
    print(f'EPUB file created: {output_file}')

create_epub("input3.txt", "output.epub", "extracted_images")
