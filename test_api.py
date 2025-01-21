import requests
import json
from typing import List, Dict, Optional
import time
import logging

# Cấu hình logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Biến lưu trữ link API
API_BASE_URL = "https://beb7-35-230-81-209.ngrok-free.app/"

class TranslationClient:
    def __init__(self, base_url: str = API_BASE_URL):
        """
        Khởi tạo client với base URL của API.
        
        Args:
            base_url (str): URL của API
        """
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.check_connection()
        
    def check_connection(self) -> bool:
        """
        Kiểm tra kết nối tới server thông qua health check endpoint.
        
        Returns:
            bool: True nếu kết nối thành công, False nếu thất bại
        """
        try:
            response = self.session.get(f"{self.base_url}/health", timeout=10)
            response.raise_for_status()
            logging.info("Successfully connected to translation server")
            return True
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to connect to server: {str(e)}")
            return False
            
    def translate_text(self, text: str) -> Optional[Dict]:
        """
        Dịch một đoạn văn bản.
        
        Args:
            text (str): Văn bản cần dịch
            
        Returns:
            Optional[Dict]: Kết quả dịch bao gồm văn bản gốc và bản dịch, hoặc None nếu có lỗi
        """
        if not text or not isinstance(text, str):
            logging.error("Invalid input: text must be a non-empty string")
            return None
            
        endpoint = f"{self.base_url}/translate"
        
        try:
            logging.info(f"Sending translation request for: {text[:100]}...")
            response = self.session.post(
                endpoint,
                json={"text": text},
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            response.raise_for_status()
            result = response.json()
            logging.info("Translation completed successfully")
            return result
            
        except requests.exceptions.Timeout:
            logging.error("Request timed out")
            return None
        except requests.exceptions.RequestException as e:
            logging.error(f"Error during translation request: {str(e)}")
            return None
            
    def batch_translate(self, 
                       texts: List[str], 
                       batch_size: int = 5, 
                       delay: float = 1.0,
                       max_retries: int = 3,
                       retry_delay: float = 2.0) -> List[Dict]:
        """
        Dịch nhiều đoạn văn bản với xử lý batch và retry.
        
        Args:
            texts (List[str]): Danh sách các văn bản cần dịch
            batch_size (int): Số lượng văn bản xử lý mỗi batch
            delay (float): Thời gian delay giữa các batch (giây)
            max_retries (int): Số lần thử lại tối đa cho mỗi request
            retry_delay (float): Thời gian chờ giữa các lần retry (giây)
            
        Returns:
            List[Dict]: Danh sách kết quả dịch
        """
        if not texts:
            logging.error("Empty input list")
            return []
            
        results = []
        
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            
            for text in batch:
                for attempt in range(max_retries):
                    result = self.translate_text(text)
                    if result:
                        results.append(result)
                        break
                    elif attempt < max_retries - 1:
                        logging.warning(f"Retrying translation after {retry_delay} seconds...")
                        time.sleep(retry_delay)
                    
            if i + batch_size < len(texts):
                logging.info(f"Waiting {delay} seconds before next batch...")
                time.sleep(delay)
                
        return results

def main():
    # Khởi tạo client
    translator = TranslationClient()
    
    # Test 1: Dịch một câu
    print("\n=== Test đơn lẻ ===")
    result = translator.translate_text("こんにちは、お元気ですか？")
    if result:
        print(f"Input: {result['source_text']}")
        print(f"Output: {result['translated_text']}")
    
    # Test 2: Dịch nhiều câu
    print("\n=== Test batch ===")
    texts = [
        "こんにちは、お元気ですか？",
        "私はプログラマーです",
        "Pythonは素晴らしいです",
        "機械学習は面白いです",
        "良い一日を！"
    ]
    
    results = translator.batch_translate(
        texts=texts,
        batch_size=2,
        delay=1.0,
        max_retries=3,
        retry_delay=2.0
    )
    
    for i, result in enumerate(results, 1):
        print(f"\nBatch item {i}:")
        print(f"Input: {result['source_text']}")
        print(f"Output: {result['translated_text']}")

if __name__ == "__main__":
    main()