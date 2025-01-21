import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
from fpdf import FPDF
import requests
import logging
import os
from tqdm import tqdm

# log to console
logging.basicConfig(level=logging.INFO)

# PDF generator class
class PDFGenerator(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 12)
        self.cell(0, 10, 'Translated Document', ln=1, align='C')
        self.ln(5)

# extract images from the book
def extract_images(book):
    images = {}
    for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
        images[item.get_name()] = item.get_content()
    return images

# create PDF from text and images
def create_pdf(text_data, images, output_pdf):
    pdf = PDFGenerator()
    pdf.add_page()
    pdf.set_font('Arial', '', 12)

    for line in text_data.split('\n'):
        pdf.multi_cell(0, 10, line)
    pdf.add_page()

    img_count = 1
    for name, content in images.items():
        img_path = f"temp_{img_count}.png"
        with open(img_path, 'wb') as f:
            f.write(content)
        pdf.image(img_path, w=100)
        pdf.ln(10)
        os.remove(img_path)
        img_count += 1

    pdf.output(output_pdf)
    logging.info(f"PDF created at {output_pdf}")

# main function
def main():
    input_epub = './epub-jp/vol-5-jp-src.epub' # input EPUB file
    output_pdf = './out-pdf/vol5-vi-v1.pdf' # output PDF file
    api_url = 'https://4546-35-231-236-189.ngrok-free.app/translate' # translation API URL

    # progress tracking
    logging.info("Reading EPUB...")
    book = epub.read_epub(input_epub)
    images = extract_images(book)

    collected_text = []
    # extract text from the book
    for doc in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(doc.get_content(), 'html.parser')
        titles = [t.get_text(strip=True) for t in soup.find_all('title')]
        paragraphs = [p.get_text(strip=True) for p in soup.find_all('p')]
        collected_text.extend(titles + paragraphs)

    translated_segments = [] # translated text segments
    logging.info("Sending extracted text to API...")
    # translate text segments
    for segment in tqdm(collected_text, desc="Translating"):
        if not segment.strip():
            translated_segments.append(segment)
            continue
        try: #catch exceptions
            resp = requests.post(api_url, json={"text": segment})
            resp.raise_for_status()
            translated_text = resp.json().get('translated_text', segment)
            translated_segments.append(translated_text)
        except Exception as e:
            logging.error(f"Translation error: {e}")
            translated_segments.append(segment)

    final_text = '\n'.join(translated_segments)
    create_pdf(final_text, images, output_pdf)

if __name__ == '__main__':
    main()