import os
import sys
import time
import threading
import tiktoken
from dotenv import load_dotenv
from openai import OpenAI

# Cấu hình hệ thống
load_dotenv()
MAX_RETRIES = 3
MIN_TOKEN_RATIO = 0.5
MAX_TOKEN_RATIO = 1.5
BATCH_SIZE = 80
API_MODEL = "deepseek/deepseek-r1:free"
PROMPT_TEMPLATE = """[QUAN TRỌNG] Dịch chính xác từ Nhật sang Việt:
1. Giữ nguyên tất cả nội dung gốc
2. Không được bỏ sót bất kỳ chi tiết nào
3. Bảo toàn cấu trúc dòng và định dạng
4. Có thể điều chỉnh dựa theo đoạn hội thoại làm thân mật hơn hoặc trịnh trọng hơn
5. Chỉ trả lời bản dịch tiếng Việt, không cần trả lời lại bản gốc hay giải thích gì thêm

Văn bản cần dịch:
{content}"""

class AdvancedTranslator:
    def __init__(self):
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("API_KEY")
        )
        self.tokenizer = tiktoken.get_encoding("cl100k_base")
        self.lock = threading.Lock()
        self.retry_counts = {}

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

    def get_log_path(self, output_path):
        return f"{os.path.splitext(output_path)[0]}.log"

    def analyze_content(self, file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            content_section = content.split('===== CONTENT =====')
            if len(content_section) < 2:
                raise ValueError("Không tìm thấy phần nội dung chính")
            
            return [
                line.strip()[3:]
                for line in content_section[1].split('\n')
                if line.startswith('P: ') and line.strip()
            ]
        except Exception as e:
            print(f"\nLỖI XỬ LÝ FILE: {str(e)}")
            sys.exit(1)

    def check_existing_progress(self, log_path):
        try:
            if not os.path.exists(log_path):
                return 0
                
            with open(log_path, 'r', encoding='utf-8') as f:
                return len([line for line in f.read().splitlines() if line.isdigit()])
        except:
            return 0

    def calculate_token_metrics(self, source, translation):
        source_text = '\n'.join(source)
        src_tokens = len(self.tokenizer.encode(source_text))
        trans_tokens = len(self.tokenizer.encode(translation))
        
        if src_tokens == 0:
            return float('inf'), src_tokens, trans_tokens
            
        ratio = trans_tokens / src_tokens
        return ratio, src_tokens, trans_tokens

    def process_batch(self, batch_text, batch_number):
        timer = self.TimerWithProgress()
        best_result = None
        best_ratio = float('inf')
        
        for attempt in range(MAX_RETRIES + 1):
            try:
                timer.start()
                response = self.client.chat.completions.create(
                    model=API_MODEL,
                    messages=[{
                        "role": "user",
                        "content": PROMPT_TEMPLATE.format(content=batch_text)
                    }],
                    temperature=0.3,
                    max_tokens=int(len(batch_text) * 3)
                )
                result = response.choices[0].message.content
                elapsed = timer.stop()

                ratio, src, trans = self.calculate_token_metrics(
                    batch_text.split('\n'), 
                    result
                )
                
                print(f"\n→ Batch {batch_number} - Lần thử {attempt+1}")
                print(f"   Token: {src} → {trans} | Tỷ lệ: {ratio:.2f}")
                
                if abs(1 - ratio) < abs(1 - best_ratio):
                    best_result = result
                    best_ratio = ratio
                
                if MIN_TOKEN_RATIO <= ratio <= MAX_TOKEN_RATIO:
                    return best_result, elapsed, ratio
                    
                print(f"   Tỷ lệ chưa đạt yêu cầu, thử lại...") 
            except Exception as e:
                elapsed = timer.stop()
                print(f"\nLỖI API: {str(e)}")
                if attempt == MAX_RETRIES:
                    return f"[LỖI: {str(e)}]", elapsed, 0
                
        return best_result, elapsed, best_ratio
    
    def execute_translation(self, input_path):
        output_path = f"{os.path.splitext(input_path)[0]}_translated.txt"
        log_path = self.get_log_path(output_path)
        content_lines = self.analyze_content(input_path)
        
        batches = [
            content_lines[i:i+BATCH_SIZE]
            for i in range(0, len(content_lines), BATCH_SIZE)
        ]
        
        processed_batches = self.check_existing_progress(log_path)
        print(f"\nĐã xử lý: {processed_batches}/{len(batches)} batch")
        print(f"Tổng số batch cần xử lý: {len(batches)}")

        total_time = 0
        for idx in range(processed_batches, len(batches)):
            batch = batches[idx]
            batch_number = idx + 1
            
            print(f"\n{'━'*40}")
            print(f"BATCH {batch_number}/{len(batches)}")
            print(f"Số dòng: {len(batch)}")
            
            result, elapsed_time, ratio = self.process_batch(
                '\n'.join(batch), 
                batch_number
            )
            total_time += elapsed_time
            
            status = ""
            if ratio < MIN_TOKEN_RATIO:
                status = f" (TỶ LỆ THẤP: {ratio:.2f})"
            elif ratio > MAX_TOKEN_RATIO:
                status = f" (TỶ LỆ CAO: {ratio:.2f})"
            
            with self.lock:
                # Ghi nội dung dịch
                with open(output_path, 'a', encoding='utf-8') as f:
                    f.write(f"\n\n--- BATCH {batch_number}{status} ---\n")
                    f.write(result)
                
                # Cập nhật log file
                with open(log_path, 'a', encoding='utf-8') as log:
                    log.write(f"{batch_number}\n")
            
            print(f"Hoàn thành trong {elapsed_time:.1f}s{status}")

        print(f"\n{'═'*40}")
        print(f"Tổng thời gian thực thi: {total_time/60:.1f} phút")
        print(f"File kết quả: {output_path}")
        print(f"File log tiến trình: {log_path}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Cách dùng: python advanced_translator.py <file_input.txt>")
        sys.exit(1)
    
    translator = AdvancedTranslator()
    translator.execute_translation(sys.argv[1])