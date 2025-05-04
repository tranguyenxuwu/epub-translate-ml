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
MAX_RETRIES = 4
MIN_TOKEN_RATIO = 0.75
MAX_TOKEN_RATIO = 1.3
BATCH_SIZE = 80
API_MODEL = "deepseek/deepseek-r1:free"
PROMPT_TEMPLATE = """[QUAN TRỌNG] Dịch chính xác từ Nhật sang Việt:
1. Chú ý đến bối cảnh và cách xưng hô của nhân vật
2. Không bỏ sót chi tiết nào
3. Các đoạn hội thoại để trong dấu "(nội dung hội thoại)"
4. Tự điều chỉnh độ trang trọng/phổ thông theo ngữ cảnh
5. Chỉ trả về bản dịch, không giải thích hoặc thêm nội dung

Nội dung trước (context), không dịch đoạn này:
{context}

Nội dung cần dịch:
{content}"""

class AdvancedTranslator:
    def __init__(self, log_callback=None, progress_callback=None): # Add callbacks
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("API_KEY")
        )
        self.tokenizer = tiktoken.get_encoding("cl100k_base")
        self.lock = threading.Lock()
        self.context_lines = []
        self.stop_flag = False
        # Use default print if no callback provided
        self.log = log_callback if log_callback else lambda msg: print(msg, flush=True)
        self.progress_update = progress_callback if progress_callback else lambda msg: sys.stdout.write(f"\r{msg}")
        # Don't handle SIGINT here if run from GUI

    class TimerWithProgress:
        def __init__(self, progress_callback=None): # Modified
            self.start_time = 0
            self.active = False
            self.thread = None
            # Use default stdout write if no callback
            self.progress_update = progress_callback if progress_callback else lambda msg: sys.stdout.write(f"\r{msg}")

        def start(self):
            self.active = True
            self.start_time = time.time()

            def display_progress():
                while self.active:
                    elapsed = time.time() - self.start_time
                    self.progress_update(f"Thời gian xử lý: {elapsed:.1f}s") # Modified
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
                
                # Xử lý dialog
                if stripped.startswith('P: '):
                    content_line = stripped[3:].strip()
                    if content_line:
                        lines.append(content_line)
                    continue
                
                # Giữ nguyên tất cả dòng khác
                lines.append(line)
            return lines
        except Exception as e:
            self.log(f"LỖI PHÂN TÍCH: {str(e)}") # Modified
            raise # Re-raise the exception for GUI handling

    def execute_translation(self, input_path):
        output_path = f"{os.path.splitext(input_path)[0]}_translated.txt"
        log_path = f"{os.path.splitext(output_path)[0]}.log"
        content_lines = self.analyze_content(input_path)
        
        # Tạo batch
        batches = []
        current_batch = []
        for line in content_lines:
            stripped_line = line.strip()
            
            # Xử lý chapter
            if stripped_line.startswith(('Chapter', 'CHAPTER', 'Chương', 'CHƯƠNG')):
                if current_batch:
                    batches.append(current_batch)
                    current_batch = []
                current_batch.append(line)
            else:
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
        
        self.log(f"Đã xử lý: {processed}/{len(valid_batches)} batch") # Modified
        self.log(f"Tổng batch hợp lệ: {len(valid_batches)}") # Modified

        for idx in range(processed, len(valid_batches)):
            if self.stop_flag:
                self.log("Dừng xử lý theo yêu cầu.") # Added log
                break
                
            batch = valid_batches[idx]
            batch_num = idx + 1
            
            # Xử lý batch chứa chapter
            first_line = batch[0].strip()
            if first_line.startswith(('Chapter', 'CHAPTER', 'Chương', 'CHƯƠNG')):
                # Ghi chapter trực tiếp
                with open(output_path, 'a', encoding='utf-8') as f:
                    f.write(f"\n--- BATCH {batch_num} ---\n{batch[0]}\n")
                with open(log_path, 'a') as log:
                    log.write(f"{batch_num}\n")
                self.log(f"→ Batch {batch_num} - Đã ghi chapter") # Modified
                
                # Xử lý phần còn lại
                remaining_lines = batch[1:]
                if remaining_lines:
                    batch_text = '\n'.join(remaining_lines)
                    result, elapsed, ratio = self.process_batch(batch_text, batch_num)
                    
                    with self.lock:
                        with open(output_path, 'a', encoding='utf-8') as f:
                            f.write(f"{result}\n")
                    self.log(f"Hoàn thành batch {batch_num} (phần còn lại) trong {elapsed:.1f}s") # Modified
                continue

            # Xử lý batch thông thường
            batch_text = '\n'.join(batch)
            result, elapsed, ratio = self.process_batch(batch_text, batch_num)
            
            # Ghi kết quả
            with self.lock:
                with open(output_path, 'a', encoding='utf-8') as f:
                    f.write(f"\n--- BATCH {batch_num} ---\n{result}")
                with open(log_path, 'a') as log:
                    log.write(f"{batch_num}\n")
            
            self.log(f"Hoàn thành batch {batch_num} trong {elapsed:.1f}s") # Modified

        self.log("Hoàn tất quá trình dịch.") # Added completion message

    def process_batch(self, batch_text, batch_num):
        timer = self.TimerWithProgress(progress_callback=self.progress_update) # Modified
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

                self.log(f"→ Batch {batch_num} - Lần {attempt+1}") # Modified
                self.log(f"   Token: {src_tokens} → {trans_tokens} | Tỷ lệ: {ratio:.2f}") # Modified
                
                # Lưu kết quả tốt nhất
                if ratio > best_ratio:
                    best_result = result
                    best_ratio = ratio

                # Đạt điều kiện
                if MIN_TOKEN_RATIO <= ratio <= MAX_TOKEN_RATIO:
                    self.context_lines = result.strip().split('\n')[-10:]
                    return result, elapsed, ratio

            except Exception as e:
                self.log(f"LỖI API (Batch {batch_num}, Lần {attempt+1}): {str(e)}") # Modified
                if attempt == MAX_RETRIES:
                    self.log(f"LỖI CUỐI CÙNG (Batch {batch_num}): {str(e)}") # Modified
                    return f"[LỖI: {str(e)}]", 0, 0
            finally:
                # Ensure timer stops cleanly even on error/stop
                if timer.active:
                    timer.stop()

        self.context_lines = best_result.split('\n')[-10:] if best_result else []
        return best_result, elapsed, best_ratio

    # New method for GUI stop button
    def request_stop(self):
        self.log("→ Yêu cầu dừng nhận được. Sẽ dừng sau batch hiện tại...")
        self.stop_flag = True

if __name__ == "__main__":
    # Setup signal handler only when run as script
    def handle_interrupt_cli(signum, frame):
        print("\n→ Yêu cầu dừng nhận được. Đang hoàn thành batch hiện tại...")
        translator.stop_flag = True # Access the global translator instance

    if len(sys.argv) != 2:
        print("Cách dùng: python translator.py <input.txt>")
        sys.exit(1)
    
    translator = AdvancedTranslator()
    signal.signal(signal.SIGINT, handle_interrupt_cli) # Setup signal handler here
    translator.execute_translation(sys.argv[1])