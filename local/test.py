import os
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO

# Đường dẫn file EPUB
epub_path = 'vol-6.epub'
# Thư mục lưu hình ảnh
image_folder = 'extracted_images'
os.makedirs(image_folder, exist_ok=True)

# Hàm lưu hình ảnh
def save_image(image_data, image_name):
    image_path = os.path.join(image_folder, image_name)
    with open(image_path, 'wb') as f:
        f.write(image_data)
    return image_path

# Đọc file EPUB
book = epub.read_epub(epub_path)
items = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))

# Biến lưu nội dung
content = []
image_log = []

# Duyệt qua các mục trong EPUB
for item in items:
    soup = BeautifulSoup(item.get_body_content(), 'html.parser')
    
    # Xử lý thẻ <title>
    title_tag = soup.find('title')
    if title_tag:
        content.append(f"TITLE: {title_tag.get_text()}")
    
    # Xử lý thẻ <h1>
    for h1_tag in soup.find_all('h1'):
        content.append(f"H1: {h1_tag.get_text()}")
    
    # Xử lý thẻ <p>
    for p_tag in soup.find_all('p'):
        content.append(f"P: {p_tag.get_text()}")
    
    # Xử lý thẻ <img>
    for img_tag in soup.find_all('img'):
        img_src = img_tag.get('src')
        if img_src:
            # Tìm hình ảnh trong các mục của EPUB
            img_item = book.get_item_with_href(img_src)
            if img_item:
                img_data = img_item.get_content()
                image = Image.open(BytesIO(img_data))
                if image.width >= 320 and image.height >= 320:
                    image_name = f"image-{img_tag.sourceline}.jpg"
                    image_path = save_image(img_data, image_name)
                    content.append(f"IMAGE: {image_path}")
                    image_log.append(f"Đã phát hiện {image_name} tại dòng {img_tag.sourceline}.")

# Ghi log hình ảnh
with open('image_log.txt', 'w', encoding='utf-8') as log_file:
    log_file.write('\n'.join(image_log))

# Ghi nội dung vào file TXT
with open('output.txt', 'w', encoding='utf-8') as output_file:
    output_file.write('\n'.join(content))