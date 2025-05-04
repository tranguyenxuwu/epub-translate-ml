import os
import sys
import time
import threading
import signal
import tiktoken
from dotenv import load_dotenv
from openai import OpenAI

# Cấu hình hệ thống
load_dotenv()
MAX_RETRIES = 3
MIN_TOKEN_RATIO = 0.8
MAX_TOKEN_RATIO = 1.3
BATCH_SIZE = 80
API_MODEL = "deepseek/deepseek-r1:free"
PROMPT_TEMPLATE = """[QUAN TRỌNG] Dịch chính xác từ Nhật sang Việt:
1. Giữ nguyên định dạng [CHAPTER] và nội dung gốc
2. Không bỏ sót chi tiết nào
3. Duy trì cấu trúc dòng và khoảng trắng
4. Tự điều chỉnh độ trang trọng/phổ thông theo ngữ cảnh
5. Chỉ trả về bản dịch, không giải thích

Nội dung trước (context):
{context}

Nội dung cần dịch:
{content}"""

class AdvancedTranslator:
    def __init__(self):
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("API_KEY")
        )
        self.tokenizer = tiktoken.get_encoding("cl100k_base")
        self.lock = threading.Lock()
        self.context_lines = []
        self.stop_flag = False
        signal.signal(signal.SIGINT, self._handle_interrupt)

    class TimerWithProgress:
        def __init__(self):
            self.start_time = 0
            self.active = False
            self.thread = None

        def start(self):
            self.active = True
            self.start_time = time.time()
            
            def display_progress():
                while self.active:
                    elapsed = time.time() - self.start_time
                    sys.stdout.write(f"\rThời gian xử lý: {elapsed:.1f}s")
                    sys.stdout.flush()
                    time.sleep(0.1)
            
            self.thread = threading.Thread(target=display_progress, daemon=True)
            self.thread.start()

        def stop(self):
            self.active = False
            if self.thread:
                self.thread.join()
            return time.time() - self.start_time

    def analyze_content(self, file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            content_section = content.split('===== CONTENT =====', 1)
            if len(content_section) < 2:
                raise ValueError("Không tìm thấy nội dung")

            lines = []
            for line in content_section[1].split('\n'):
                stripped = line.strip()
                if not stripped:
                    continue  # Bỏ qua dòng trống
                
                # Xử lý chapter
                if stripped.startswith(('Chapter', 'CHAPTER', 'Chương', 'CHƯƠNG')):
                    lines.append(f"[CHAPTER] {stripped}")
                    continue
                
                # Xử lý dialog
                if stripped.startswith('P: '):
                    content_line = stripped[3:].strip()
                    if content_line:
                        lines.append(content_line)
            return lines
        except Exception as e:
            print(f"LỖI PHÂN TÍCH: {str(e)}")
            sys.exit(1)

    def execute_translation(self, input_path):
        output_path = f"{os.path.splitext(input_path)[0]}_translated.txt"
        log_path = f"{os.path.splitext(output_path)[0]}.log"
        content_lines = self.analyze_content(input_path)
        
        # Tạo batch
        batches = []
        current_batch = []
        for line in content_lines:
            # Xử lý chapter
            if line.startswith('[CHAPTER]'):
                if current_batch:
                    batches.append(current_batch)
                    current_batch = []
                batches.append([line])
                continue
            
            current_batch.append(line)
            if len(current_batch) >= BATCH_SIZE:
                batches.append(current_batch)
                current_batch = []
        
        if current_batch:
            batches.append(current_batch)
        
        # Lọc batch không hợp lệ
        valid_batches = []
        for batch in batches:
            if any(line.strip() for line in batch):
                valid_batches.append(batch)
        
        # Kiểm tra tiến độ
        processed = 0
        if os.path.exists(log_path):
            with open(log_path, 'r') as f:
                processed = len(f.read().splitlines())
        
        print(f"Đã xử lý: {processed}/{len(valid_batches)} batch")
        print(f"Tổng batch hợp lệ: {len(valid_batches)}")

        for idx in range(processed, len(valid_batches)):
            if self.stop_flag:
                break
                
            batch = valid_batches[idx]
            batch_num = idx + 1
            
            # Xử lý chapter đặc biệt
            if len(batch) == 1 and batch[0].startswith('[CHAPTER]'):
                with open(output_path, 'a', encoding='utf-8') as f:
                    f.write(f"\n--- BATCH {batch_num} ---\n{batch[0]}\n")
                with open(log_path, 'a') as log:
                    log.write(f"{batch_num}\n")
                print(f"\n→ Batch {batch_num} - Đã ghi chapter")
                continue

            print(f"\n{'─'*40}")
            print(f"BATCH {batch_num}/{len(valid_batches)}")
            print(f"Số dòng: {len(batch)}")
            
            batch_text = '\n'.join(batch)
            result, elapsed, ratio = self.process_batch(batch_text, batch_num)
            
            # Ghi kết quả
            with self.lock:
                with open(output_path, 'a', encoding='utf-8') as f:
                    f.write(f"\n--- BATCH {batch_num} ---\n{result}")
                with open(log_path, 'a') as log:
                    log.write(f"{batch_num}\n")
            
            print(f"\nHoàn thành trong {elapsed:.1f}s")

    def process_batch(self, batch_text, batch_num):
        timer = self.TimerWithProgress()
        best_result = None
        best_ratio = float('inf')
        
        # Tạo prompt
        prompt = PROMPT_TEMPLATE.format(
            context='\n'.join(self.context_lines[-10:]),
            content=batch_text
        )
        
        for attempt in range(MAX_RETRIES + 1):
            if self.stop_flag:
                break
                
            try:
                timer.start()
                response = self.client.chat.completions.create(
                    model=API_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=int(len(batch_text)*3)
                )
                result = response.choices[0].message.content
                elapsed = timer.stop()

                # Kiểm tra token ratio
                src_tokens = len(self.tokenizer.encode(batch_text))
                trans_tokens = len(self.tokenizer.encode(result))
                ratio = trans_tokens / src_tokens if src_tokens else 0

                print(f"\n→ Batch {batch_num} - Lần {attempt+1}")
                print(f"   Token: {src_tokens} → {trans_tokens} | Tỷ lệ: {ratio:.2f}")
                
                # Lưu kết quả tốt nhất
                if ratio > best_ratio:
                    best_result = result
                    best_ratio = ratio

                # Đạt điều kiện
                if MIN_TOKEN_RATIO <= ratio <= MAX_TOKEN_RATIO:
                    self.context_lines = result.strip().split('\n')[-10:]
                    return result, elapsed, ratio

            except Exception as e:
                print(f"LỖI API: {str(e)}")
                if attempt == MAX_RETRIES:
                    return f"[LỖI: {str(e)}]", 0, 0

        self.context_lines = best_result.split('\n')[-10:] if best_result else []
        return best_result, elapsed, best_ratio

    def _handle_interrupt(self, signum, frame):
        print("\n→ Yêu cầu dừng nhận được. Đang hoàn thành batch hiện tại...")
        self.stop_flag = True

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Cách dùng: python translator.py <input.txt>")
        sys.exit(1)
    
    translator = AdvancedTranslator()
    translator.execute_translation(sys.argv[1])