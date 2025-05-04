import os
from dotenv import load_dotenv
import time
from openai import OpenAI
import threading
import sys

# Load environment variables from .env file
load_dotenv()

class ElapsedTimeDisplay:
    def __init__(self, update_interval=0.11):
        self.update_interval = update_interval
        self.stop_event = threading.Event()
        self.thread = None
        self.start_time = None

    def start(self):
        self.start_time = time.time()
        self.stop_event.clear()
        
        def update_display():
            while not self.stop_event.is_set():
                elapsed = time.time() - self.start_time
                sys.stdout.write(f"\rTime elapsed: {elapsed:.2f} seconds")
                sys.stdout.flush()
                time.sleep(self.update_interval)
                
        self.thread = threading.Thread(target=update_display)
        self.thread.daemon = True
        self.thread.start()
    
    def stop(self):
        if self.thread and self.thread.is_alive():
            self.stop_event.set()
            self.thread.join(timeout=0.1)
            final_time = time.time() - self.start_time
            print(f"\nTotal time: {final_time:.2f} seconds")
            return final_time
        return 0

client = OpenAI(
  base_url="https://openrouter.ai/api/v1",
  api_key=os.environ.get("API_KEY"),
)

# Create timer display object
timer = ElapsedTimeDisplay()

# Start the timer
timer.start()

completion = client.chat.completions.create(
  extra_body={},
  model="deepseek/deepseek-r1:free",
  messages=[
    {
      "role": "user",
      "content": """ Dịch đoạn light novel này sang tiếng việt, chú ý đến ngữ cảnh của câu chuyện. danh xưng giữa Mizuki và Kazuto có thể dùng là "cậu" và "tớ" , giữ lại những hậu tố "-kun", "-san", "-chan" 
      その日の夕方。家でネトゲをしていると水樹さんから電話がかかってきた。
      「……？」
      電話とは珍しいなと思いつつ、採掘を中断してスマホを取る。
        """
    }
  ]
)

# Stop the timer display and get the final time
elapsed_time = timer.stop()

# Print the response
print(completion.choices[0].message.content)