import os
import sys
import time
import threading
import signal
import json
import xml.etree.ElementTree as ET
from xml.dom import minidom
import tiktoken
from dotenv import load_dotenv
from openai import OpenAI

# --- Configuration ---
load_dotenv()
API_KEY = os.getenv("API_KEY")
BASE_URL = "https://openrouter.ai/api/v1"
API_MODEL = "deepseek/deepseek-r1-0528:free" # Or your preferred model
MAX_RETRIES = 3
MIN_TOKEN_RATIO = 0.6 # Adjust as needed
MAX_TOKEN_RATIO = 1.5 # Adjust as needed
BATCH_SIZE = 70 # Number of text elements per API call
CONTEXT_WINDOW = 15 # Number of previous translations to use as context
OUTPUT_ENCODING = 'utf-8'
PROGRESS_FILE_SUFFIX = '_progress.json'
TRANSLATED_FILE_SUFFIX = '_translated.xml'
#RSE = "medium" # reasoning_effort, can be "low", "medium", "high", comment out if use non-google models

PROMPT_TEMPLATE = """
[IMPORTANT] Translate the following English light novel text snippets into Vietnamese. Follow these instructions carefully:

1. Use a natural, emotionally expressive, and polished Vietnamese writing style suitable for publication — avoid mechanical or overly literal translations.
2. Translate completely and faithfully; do not omit, simplify, or summarize any content.
3. Adapt sentence structure and word choices to match natural Vietnamese phrasing, as long as the original nuance, tone, and character emotion are preserved.
4. Enclose all dialogue in Vietnamese-style quotation marks, like this: "translated dialogue".
5. Ensure each character’s speech reflects their personality and emotional state — whether shy, blunt, teasing, sincere, or flustered.
6. Each line of input is in the format id: text_to_translate. Return the result as id: translated_text.
7. Output only the translated lines, no extra commentary or explanation — one line per input ID.

Contextual Information (do not translate):

This light novel is a wholesome, zero-misunderstanding romantic comedy focused on Sasahara Naoya and Shirogane Koyuki — a shy but sharp-tongued girl who struggles to express her feelings honestly. After Naoya’s sincere confession, their relationship gradually deepens. The story emphasizes emotional honesty, quiet intimacy, gentle romantic tension, and meaningful character growth through small everyday moments.

Characters:
- Sasahara Naoya: Kind, emotionally open, and straightforward. He’s calm and steady, giving Koyuki space while gently supporting her.
- Shirogane Koyuki: Intelligent, sarcastic, and easily flustered. She hides her affection behind cutting remarks, but is gradually learning to open her heart.

Tone & Style:
- The narration should be fluid, warm, and human — like a well-translated light novel, not a textbook.
- Dialogue must feel emotionally real, true to character, and appropriately casual/formal depending on the scene.
- Capture the subtle sweetness, awkward tension, and everyday charm of the characters’ growing relationship.

Please translate the following with this context in mind.

Previous context (do not translate this part):
{context}

Content to translate (each item on a new line, formatted as 'id: text_to_translate'):
{content}

"""

# --- Globals ---
stop_flag = False

# --- Helper Classes ---
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
                sys.stdout.write(f"\rAPI Call Time: {elapsed:.1f}s")
                sys.stdout.flush()
                time.sleep(0.1)

        self.thread = threading.Thread(target=display_progress, daemon=True)
        self.thread.start()

    def stop(self):
        self.active = False
        if self.thread:
            self.thread.join()
        sys.stdout.write("\r" + " " * 30 + "\r") # Clear the line
        sys.stdout.flush()
        return time.time() - self.start_time

# --- Core Translator Class ---
class XMLTranslator:
    def __init__(self, input_xml_path):
        self.input_xml_path = input_xml_path
        self.output_xml_path = self._generate_output_path(input_xml_path, TRANSLATED_FILE_SUFFIX)
        self.progress_file = self._generate_output_path(input_xml_path, PROGRESS_FILE_SUFFIX)

        if not API_KEY:
            raise ValueError("ERROR: API_KEY not found in environment variables or .env file.")

        self.client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
        try:
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            print(f"Error initializing tokenizer: {e}. Using basic split.")
            self.tokenizer = None # Fallback

        self.lock = threading.Lock()
        self.translation_cache = {}
        self.context_lines = []
        self.elements_to_translate = []
        # Initialize with default values
        self.prompt_template = PROMPT_TEMPLATE
        self.model = API_MODEL
        self.batch_size = BATCH_SIZE
        # Initialize interrupt flag
        self._interrupt_requested = False
        self.consecutive_rate_limit_errors = 0
        self.rate_limit_stop_triggered = False

    def _generate_output_path(self, base_path, suffix):
        base_name = os.path.splitext(base_path)[0]
        return f"{base_name}{suffix}"

    def _handle_interrupt(self, signum, frame):
        global stop_flag
        print("\n-> Stop request received. Finishing current batch and saving progress...")
        stop_flag = True

    def load_progress(self):
        if os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, 'r', encoding=OUTPUT_ENCODING) as f:
                    self.translation_cache = json.load(f)
                print(f"Loaded progress from {self.progress_file}. {len(self.translation_cache)} elements already translated.")
                # --- Add Debug Print ---
                loaded_keys = list(self.translation_cache.keys())
                print(f"DEBUG: Loaded {len(loaded_keys)} cache keys. First 10: {loaded_keys[:10]}")
                # --- End Debug Print ---
            except json.JSONDecodeError:
                # --- Make error more prominent ---
                print(f"ERROR: Could not decode progress file {self.progress_file}. It might be corrupted. Starting fresh.")
                self.translation_cache = {}
            except Exception as e:
                # --- Make error more prominent ---
                print(f"ERROR: Error loading progress file {self.progress_file}: {e}. Starting fresh.")
                self.translation_cache = {}
        else:
            print("No progress file found. Starting fresh.")
            self.translation_cache = {}

    def save_progress(self):
        try:
            with self.lock:
                with open(self.progress_file, 'w', encoding=OUTPUT_ENCODING) as f:
                    json.dump(self.translation_cache, f, ensure_ascii=False, indent=2)
                # print(f"Progress saved to {self.progress_file}")
        except Exception as e:
            print(f"Error saving progress: {e}")

    def extract_translatable_elements(self):
        print(f"Parsing XML file: {self.input_xml_path}")
        try:
            tree = ET.parse(self.input_xml_path)
            root = tree.getroot()
            self.elements_to_translate = []

            for chapter in root.findall('.//chapter'):
                chapter_id = chapter.get('id')
                chapter_title = chapter.get('title')
                if chapter_id and chapter_title:
                    # Add chapter title for translation
                    self.elements_to_translate.append({'id': f"{chapter_id}_title", 'text': chapter_title, 'type': 'chapter'})

                for elem in chapter:
                    if elem.tag == 'paragraph' and elem.get('translate') == 'yes':
                        para_id = elem.get('id')
                        text_elem = elem.find('text')
                        if para_id and text_elem is not None and text_elem.text:
                            self.elements_to_translate.append({'id': para_id, 'text': text_elem.text.strip(), 'type': 'paragraph'})
                    # Keep track of images or other elements if needed for context, but don't add to translate list
                    # elif elem.tag == 'image':
                    #     pass # Handle images if necessary for context

            print(f"Found {len(self.elements_to_translate)} elements potentially needing translation.")
            return True
        except ET.ParseError as e:
            print(f"Error parsing XML file: {e}")
            return False
        except Exception as e:
            print(f"An unexpected error occurred during XML parsing: {e}")
            return False

    def translate_elements(self):
        self.load_progress() # Load first
        # Ensure elements are extracted *before* checking cache against them
        if not self.extract_translatable_elements(): # Call extract here if not called in run()
             print("Error: Could not extract elements. Aborting.")
             return
        # --- Debug prints from previous step can remain or be removed ---
        # print(f"DEBUG: Total elements extracted: {len(self.elements_to_translate)}")
        # print(f"DEBUG: Total elements in loaded cache: {len(self.translation_cache)}")
        if self.elements_to_translate:
             first_elem_id = self.elements_to_translate[0]['id']
             print(f"DEBUG: Checking first extracted element ID '{first_elem_id}' against loaded cache keys.")
             print(f"DEBUG: Is '{first_elem_id}' in cache? {first_elem_id in self.translation_cache}")
             # Check a later element too, if available
             if len(self.elements_to_translate) > self.batch_size:
                 try: # Add try-except in case elements_to_translate is shorter than batch_size
                     later_elem_id = self.elements_to_translate[self.batch_size]['id']
                     print(f"DEBUG: Checking element ID '{later_elem_id}' (around batch 2 start) against loaded cache keys.")
                     print(f"DEBUG: Is '{later_elem_id}' in cache? {later_elem_id in self.translation_cache}")
                 except IndexError:
                     pass # Not enough elements for this check

        # --- End Debug Print ---

        # Initial filtering of elements needing translation
        elements_to_process = [
            elem for elem in self.elements_to_translate
            if elem['id'] not in self.translation_cache
        ]
        print(f"Initially found {len(elements_to_process)} elements needing translation.")

        processed_count_in_this_run = 0
        batch_num_overall = 0 # For print statements
        original_total_to_translate = len(elements_to_process) # For percentage calculation relative to start of this run

        while elements_to_process and not stop_flag and not self._check_interrupt(): # Loop while there are items and no stop signal
            # Additional check at start of each iteration
            if self._check_interrupt():
                print("Translation interrupted by user request.")
                break
                
            batch_num_overall += 1
            current_batch_size = min(len(elements_to_process), self.batch_size)
            batch = elements_to_process[:current_batch_size]

            if not batch: # Should ideally not be reached if elements_to_process is not empty
                break

            batch_ids = [elem['id'] for elem in batch]
            batch_texts_with_ids = [f"{elem['id']}: {elem['text']}" for elem in batch]
            
            # --- Progress Print Statement ---
            current_progress_count = original_total_to_translate - len(elements_to_process)
            print(f"\nProcessing batch {batch_num_overall}: "
                  f"Attempting {len(batch)} elements (approx items {current_progress_count + 1} to {current_progress_count + len(batch)} of {original_total_to_translate} initial). "
                  f"Remaining in queue: {len(elements_to_process)}. Starting ID: {batch_ids[0] if batch_ids else 'N/A'}")
            # --- End Progress Print Statement ---

            # Check for interrupt before processing batch
            if self._check_interrupt():
                print("Translation interrupted before processing batch.")
                break

            translations_tuple = self.process_batch(batch_texts_with_ids, batch_ids)
            translations, is_complete = translations_tuple if translations_tuple else (None, False)

            successfully_translated_ids_in_this_batch = set()

            if translations:
                with self.lock:
                    for elem_id, translation_text in translations:
                        if translation_text and translation_text.strip():
                            if elem_id not in self.translation_cache: # Only count and cache if new
                                self.translation_cache[elem_id] = translation_text
                                successfully_translated_ids_in_this_batch.add(elem_id)
                        else:
                            print(f"   Warning: Skipping empty translation received for ID: {elem_id}")
                
                if successfully_translated_ids_in_this_batch:
                    processed_count_in_this_run += len(successfully_translated_ids_in_this_batch)
                    self.save_progress() # Save after successful new translations in batch

                num_returned_translations = len(translations)
                
                if is_complete:
                    print(f"Batch translated. API returned {num_returned_translations} translations. Added {len(successfully_translated_ids_in_this_batch)} new items to cache. Processed in this run: {processed_count_in_this_run}. Total in cache: {len(self.translation_cache)}. Progress saved.")
                else: # Partial success or mismatch from API perspective
                    print(f"Warning: Processed partial batch. API returned {num_returned_translations} translations for {len(batch)} sent items. Added {len(successfully_translated_ids_in_this_batch)} new items to cache. Processed in this run: {processed_count_in_this_run}. Total in cache: {len(self.translation_cache)}. Progress saved. Unreturned/mismatched items will be re-attempted if still pending.")

            elif stop_flag:
                 print("Batch processing interrupted by stop_flag.")
                 break 
            else: # Batch failed completely (process_batch returned None, False)
                print(f"Warning: Batch failed completely after retries (starting with original ID: {batch[0]['id'] if batch else 'N/A'}). These {len(batch)} items will remain in queue for the next attempt.")

            # Update elements_to_process: remove items that are now in the main cache
            elements_to_process = [
                elem for elem in elements_to_process
                if elem['id'] not in self.translation_cache
            ]
            
            if not elements_to_process:
                print("All elements processed, or no new elements were translated in the last batch and queue is now empty.")
                break
            
            # Optional: Add a small delay if API calls are rapid and failing, to prevent hammering
            # if not translations and not stop_flag and not successfully_translated_ids_in_this_batch:
            #     print("Pausing briefly after a batch yielded no new translations...")
            #     time.sleep(REQUEST_DELAY_ON_FAIL) # Define REQUEST_DELAY_ON_FAIL, e.g., 5 seconds

        print(f"\nTranslation process finished or interrupted. Total new items processed in this run: {processed_count_in_this_run}.")
        self.save_progress() # Final save

    def process_batch(self, batch_texts_with_ids, original_batch_ids): # original_batch_ids for error reporting if needed
        # Check stop flag before starting batch processing
        global stop_flag
        if stop_flag:
            print("Stop flag detected, skipping batch processing.")
            return None, False
            
        timer = TimerWithProgress()
        # best_result_lines = None # These variables seem unused
        # best_avg_ratio = -1

        # Prepare content for prompt
        content_to_translate = "\n".join(batch_texts_with_ids)

        # Prepare context
        context_str = "\n".join(self.context_lines[-CONTEXT_WINDOW:])

        prompt = self.prompt_template.format(
            context=context_str if context_str else "N/A",
            content=content_to_translate
        )

        src_tokens = len(self.tokenizer.encode(content_to_translate)) if self.tokenizer else len(content_to_translate.split())

        for attempt in range(MAX_RETRIES):
            if stop_flag or self._check_interrupt():
                print("Translation interrupted during API retry loop.")
                return None, False # Interrupted
            try:
                timer.start()
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    #temperature=0.3,
                    # max_tokens can be tricky; estimate based on source length + buffer
                    max_tokens=max(150 * len(batch_texts_with_ids), int(src_tokens * MAX_TOKEN_RATIO * 1.2) + 50),
                    # extra_body={ # Add Gemini-specific parameters here
                    #     "reasoning_effort": RSE  # Or "medium", "high" as needed
                    # }
                )
                elapsed = timer.stop() # Stop timer regardless of content validity
                self.consecutive_rate_limit_errors = 0

                # --- Check for valid content before stripping ---
                if response.choices and response.choices[0].message and response.choices[0].message.content is not None:
                    result = response.choices[0].message.content.strip()
                else:
                    # Handle the case where content is None - THIS SHOULD RETRY
                    print(f"\n   Attempt {attempt + 1}/{MAX_RETRIES}: Failed - API returned None content. Retrying...")
                    time.sleep(2 ** attempt) # Exponential backoff
                    continue # Go to the next attempt

                raw_result_lines = result.split('\n')
                parsed_translations = [] # List of (id, text) tuples
                malformed_lines = 0

                for line in raw_result_lines:
                    if not line.strip(): # Skip empty lines from API response
                        continue
                    try:
                        # Expecting format "id: translated text"
                        parts = line.split(':', 1)
                        if len(parts) == 2:
                            elem_id = parts[0].strip()
                            translated_text = parts[1].strip()
                            # Optional: Validate if elem_id is one of the sent IDs, though this adds complexity
                            # if elem_id in original_batch_ids: # original_batch_ids was passed to process_batch
                            parsed_translations.append((elem_id, translated_text))
                            # else:
                            #     print(f"   Warning: Received unexpected ID '{elem_id}' from API. Skipping.")
                            #     malformed_lines +=1
                        else:
                            # Line doesn't match "id: text" format
                            print(f"   Warning: Malformed translation line (no colon or unexpected format): '{line[:100]}...' Skipping.")
                            malformed_lines += 1
                    except Exception as e:
                        print(f"   Error parsing translation line '{line[:100]}...': {e}. Skipping.")
                        malformed_lines += 1

                if not parsed_translations and result: # No valid ID:text pairs parsed, but got some result
                    print(f"\n   Attempt {attempt + 1}/{MAX_RETRIES}: Failed - No valid 'id: text' lines parsed from API response. Response: {result[:200]}... Retrying...")
                    time.sleep(2 ** attempt)
                    continue

                # Check if the number of *parsed valid translations* matches the number of *sent items*
                is_complete_match = len(parsed_translations) == len(batch_texts_with_ids)

                if parsed_translations: # We got at least one valid (id, text) pair
                    trans_tokens = len(self.tokenizer.encode(result)) if self.tokenizer else len(result.split())
                    avg_ratio = (trans_tokens / src_tokens) if src_tokens else 0
                    status_message = "Success!" if is_complete_match else "Partial Success - ID/Line count mismatch."
                    print(f"   Attempt {attempt + 1}/{MAX_RETRIES}: {status_message} Time: {elapsed:.1f}s, Avg Ratio: {avg_ratio:.2f}")
                    print(f"    Sent {len(batch_texts_with_ids)} items, received and parsed {len(parsed_translations)} valid 'id: text' translations.")
                    if malformed_lines > 0:
                        print(f"    Additionally, {malformed_lines} lines from API were malformed or skipped.")

                    # Update context with the text part of new translations
                    self.context_lines.extend([t[1] for t in parsed_translations])
                    self.context_lines = self.context_lines[-CONTEXT_WINDOW*2:]
                    return parsed_translations, is_complete_match
                else: # No translations parsed, and it wasn't caught by 'if not parsed_translations and result:' (e.g. API returned only whitespace)
                    print(f"\n   Attempt {attempt + 1}/{MAX_RETRIES}: Failed - API returned empty or unparseable result. Retrying...")
                    time.sleep(2 ** attempt)
                    continue

            except Exception as e: # API errors (including 429) or other issues - THIS SHOULD RETRY
                elapsed = timer.stop() # Ensure timer stops on exception too
                error_type = type(e).__name__
                status_code = getattr(e, "status_code", None)
                response_obj = getattr(e, "response", None)
                if status_code is None and response_obj is not None:
                    status_code = getattr(response_obj, "status_code", None)
                message_text = str(e)
                if status_code is None and "429" in message_text:
                    status_code = 429

                print(f"\n   Attempt {attempt + 1}/{MAX_RETRIES}: API Error ({elapsed:.1f}s): {error_type} - {e}")
                print(f"   Failed Batch Content (first 100 chars): {content_to_translate[:100]}...")

                is_rate_limit = status_code == 429
                if is_rate_limit:
                    self.consecutive_rate_limit_errors += 1
                    print(
                        "   Rate limit detected (HTTP 429). Consecutive occurrences: "
                        f"{self.consecutive_rate_limit_errors}."
                    )
                    if self.consecutive_rate_limit_errors >= 5:
                        print(
                            "   Received 5 consecutive HTTP 429 responses. Stopping translation to avoid further rate limiting."
                        )
                        self.rate_limit_stop_triggered = True
                        stop_flag = True
                        self._interrupt_requested = True
                        return None, False
                else:
                    self.consecutive_rate_limit_errors = 0

                if attempt == MAX_RETRIES - 1:
                    print("   Max retries reached for this batch. Skipping.")
                    return None, False # Failed after retries

                wait_time = 2 ** attempt + 1
                if is_rate_limit:
                    wait_time = max(wait_time, 5)
                print(f"   Retrying in {wait_time} seconds...")
                time.sleep(wait_time) # Exponential backoff

        return None, False # Failed all retries

    def rebuild_xml(self):
        print(f"\nRebuilding XML with translations from cache ({len(self.translation_cache)} entries)...")
        try:
            tree = ET.parse(self.input_xml_path)
            root = tree.getroot()
            missing_translations = 0

            for chapter in root.findall('.//chapter'):
                chapter_id = chapter.get('id')
                title_id = f"{chapter_id}_title"

                # Update chapter title
                if title_id in self.translation_cache:
                    chapter.set('title', self.translation_cache[title_id])
                elif chapter.get('title'): # Only count as missing if it was supposed to be translated
                    print(f"Warning: Missing translation for chapter title ID: {title_id}")
                    missing_translations += 1

                # Update paragraphs
                for elem in chapter.findall('.//paragraph[@translate=\'yes\']'):
                    para_id = elem.get('id')
                    text_elem = elem.find('text')
                    if para_id and text_elem is not None:
                        if para_id in self.translation_cache:
                            text_elem.text = self.translation_cache[para_id]
                        elif text_elem.text: # Only count as missing if it had text and was marked translate=yes
                            print(f"Warning: Missing translation for paragraph ID: {para_id}")
                            missing_translations += 1

            if missing_translations > 0:
                print(f"Warning: {missing_translations} elements marked for translation were not found in the cache.")

            # Save the modified tree
            xml_content_bytes = ET.tostring(root, encoding=OUTPUT_ENCODING, method='xml')
            try:
                # Pretty print using minidom
                dom = minidom.parseString(xml_content_bytes)
                pretty_xml_content = dom.toprettyxml(indent="  ", encoding=OUTPUT_ENCODING)
            except Exception as pretty_print_error:
                 print(f"Warning: Could not pretty-print XML, saving raw version: {pretty_print_error}")
                 # Add XML declaration manually if pretty-printing fails
                 xml_declaration = f'<?xml version="1.0" encoding="{OUTPUT_ENCODING}"?>\n'.encode(OUTPUT_ENCODING)
                 pretty_xml_content = xml_declaration + xml_content_bytes

            with open(self.output_xml_path, 'wb') as f:
                f.write(pretty_xml_content)

            print(f"Successfully rebuilt and saved translated XML to: {self.output_xml_path}")
            return True

        except ET.ParseError as e:
            print(f"Error parsing original XML file during rebuild: {e}")
            return False
        except Exception as e:
            print(f"An unexpected error occurred during XML rebuild: {e}")
            return False

    def set_custom_prompt(self, custom_prompt: str):
        """Set a custom prompt template for translation"""
        self.prompt_template = custom_prompt
        print(f"Custom prompt template set. Length: {len(custom_prompt)} characters")
    
    def set_model(self, model_name: str):
        """Set a custom model for translation"""
        self.model = model_name
        print(f"Translation model set to: {model_name}")
    
    def set_batch_size(self, batch_size: int):
        """Set a custom batch size for translation"""
        self.batch_size = batch_size
        print(f"Batch size set to: {batch_size}")

    def stop_translation(self):
        """Stop the current translation process"""
        global stop_flag
        stop_flag = True
        print("Stop signal sent. Translation will finish current batch and save progress.")
        # Also try to interrupt any ongoing API call by setting a flag
        self._interrupt_requested = True
    
    def _check_interrupt(self):
        """Check if translation should be interrupted"""
        global stop_flag
        return stop_flag or getattr(self, '_interrupt_requested', False)
    
    def get_progress_info(self):
        """Get current translation progress information"""
        self.load_progress()
        if not hasattr(self, 'elements_to_translate') or not self.elements_to_translate:
            self.extract_translatable_elements()
        
        total_elements = len(self.elements_to_translate) if self.elements_to_translate else 0
        translated_elements = len(self.translation_cache)
        
        return {
            'total_elements': total_elements,
            'translated_elements': translated_elements,
            'completion_percentage': (translated_elements / total_elements * 100) if total_elements > 0 else 0,
            'remaining_elements': total_elements - translated_elements
        }

    def reset_interrupt_flag(self):
        """Reset the interrupt flag for a new translation session"""
        self._interrupt_requested = False
        global stop_flag
        stop_flag = False
        print("Translation flags reset. Ready for new translation.")

    def run(self):
        # Reset interrupt flag at start of new run
        self.reset_interrupt_flag()
        
        if not self.extract_translatable_elements():
            return
        self.translate_elements()
        if not stop_flag and not self._check_interrupt():
            self.rebuild_xml()
        else:
            print("Process stopped before rebuilding XML. Run again to complete.")