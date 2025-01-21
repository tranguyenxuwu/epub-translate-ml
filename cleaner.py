import re

def clean_html_and_text(file_path, output_file_name):
    with open(file_path, 'r', encoding='utf-8') as file:
        content = file.readlines()  # Đọc từng dòng

    cleaned_content = []
    empty_line_count = 0

    for line in content:
        line = line.strip()  # Loại bỏ khoảng trắng thừa ở đầu và cuối dòng

        # Xử lý thẻ <i> để giữ lại lời thoại và bao quanh nó bằng dấu ngoặc kép
        # Điều này sẽ đảm bảo không bị trùng lặp dấu ngoặc kép nếu thẻ <i> lồng nhau
        line = re.sub(r'<i>(.*?)</i>', r'"\1"', line)

        # Thêm xuống dòng sau thẻ mở HTML và trước thẻ đóng HTML
        line = re.sub(r'<(\w+)>', r'<\1>\n', line)  # Thêm xuống dòng sau thẻ mở
        line = re.sub(r'</(\w+)>', r'\n</\1>', line)  # Thêm xuống dòng trước thẻ đóng
        
        # Loại bỏ các thẻ HTML còn lại
        line = re.sub(r'<[^>]+>', '', line)

        # Loại bỏ dòng chỉ chứa dấu chấm hoặc các ký tự không cần thiết
        if re.match(r"^[\.]+$", line) or not line:
            empty_line_count += 1
            if empty_line_count <= 2:
                cleaned_content.append('')  # Thêm dòng trống nếu có
            continue
        else:
            empty_line_count = 0  # Đặt lại bộ đếm dòng trống liên tiếp
            cleaned_content.append(line)

    # Tạo file đầu ra mới
    with open(output_file_name, 'w', encoding='utf-8') as output_file:
        for line in cleaned_content:
            output_file.write(line + '\n')

# Đường dẫn file input và tên file output
input_file = "vol-5v2.txt"  # File đầu vào chứa nội dung cần xử lý
output_file_name = "./cleaned/output_vol5v8.txt"  # File đầu ra được tạo mới
clean_html_and_text(input_file, output_file_name)
print(f"Đã xử lý xong văn bản. Kết quả được lưu vào {output_file_name}")
