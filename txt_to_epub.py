from ebooklib import epub
import os

def txt_to_ebook(txt_file):
    # Create an eBook object
    book = epub.EpubBook()

    # Metadata for the eBook
    book.set_title("Vợ trong game của tôi là idol nổi tiếng ngoài đời - Tập 1 (GTL) ")
    book.set_language("vi")
    book.add_author("tranguyenxuwu")

    # Add CSS style
    style = '''
        @page { margin: 0px; }
        h1 { 
            font-size: 2em;
            font-weight: bold;
            text-align: left;
            margin-top: 2em;
            page-break-before: always;
        }
    '''
    nav_css = epub.EpubItem(uid="style_nav",
                          file_name="style/nav.css",
                          media_type="text/css",
                          content=style)
    book.add_item(nav_css)

    # Read content from the txt file
    with open(txt_file, 'r', encoding='utf-8') as file:
        lines = file.readlines()

    # Initialize variables
    chapters = []
    current_chapter = None
    current_content = []

    # Parse the txt file
    for line in lines:
        line = line.strip()
        if line.startswith("H1:"):  # New chapter heading
            if current_chapter:
                # Add the previous chapter to the book
                chapter_content = f'<h1>{current_chapter.title}</h1>'
                chapter_content += '<p>' + '</p><p>'.join(current_content) + '</p>'
                current_chapter.content = chapter_content
                book.add_item(current_chapter)
                chapters.append(current_chapter)

            # Create a new chapter
            chapter_title = line[3:].strip()
            current_chapter = epub.EpubHtml(title=chapter_title, 
                                          file_name=f'{chapter_title}.xhtml',
                                          lang='vi')
            current_chapter.add_item(nav_css)
            current_content = []

        elif line.startswith("P:"):  # Paragraph
            current_content.append(line[2:].strip() + '<br/>')

    # Add the last chapter
    if current_chapter:
        chapter_content = f'<h1>{current_chapter.title}</h1>'
        chapter_content += '<p>' + '</p><p>'.join(current_content) + '</p>'
        current_chapter.content = chapter_content
        book.add_item(current_chapter)
        chapters.append(current_chapter)

    # Organize chapters into Table of Contents and spine
    # book.toc = chapters
    # book.spine = ['nav'] + chapters

    # Add navigation files
    # book.add_item(epub.EpubNcx())
    # book.add_item(epub.EpubNav())

    # Generate output file name
    output_file = os.path.splitext(txt_file)[0] + '.epub'

    # Write the eBook to a file
    epub.write_epub(output_file, book, {})

    print(f"eBook created successfully: {output_file}")

# Example usage
txt_file = 'input3.txt'
txt_to_ebook(txt_file)