import customtkinter
import tkinter as tk # Keep for filedialog and StringVar
from tkinter import filedialog
import threading
import os
import sys
# import api_call.py - Assuming this logic is correct

# --- Set CustomTkinter Appearance ---
customtkinter.set_appearance_mode("System") # Options: "System", "Dark", "Light"
customtkinter.set_default_color_theme("blue") # Options: "blue", "green", "dark-blue"

# Add the deepseek directory to the Python path
# This assumes gui_app.py is in the parent directory of deepseek
script_dir = os.path.dirname(os.path.abspath(__file__))
deepseek_dir = os.path.join(script_dir, 'deepseek')
if deepseek_dir not in sys.path:
    sys.path.insert(0, deepseek_dir)

try:
    # Make sure the AdvancedTranslator class exists in api_call.py
    # and accepts log_callback and progress_callback in its __init__
    from api_call import AdvancedTranslator
except ImportError as e:
    print(f"Error importing AdvancedTranslator: {e}")
    print("Ensure gui_app.py is in the 'projekt' directory and 'api_call.py' is in the 'deepseek' subdirectory.")
    print("Also ensure 'api_call.py' defines the 'AdvancedTranslator' class.")
    sys.exit(1)
except AttributeError:
    print(f"Error: AdvancedTranslator class might be missing or incorrectly defined in api_call.py.")
    sys.exit(1)


class TranslatorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Advanced Translator GUI (CustomTkinter)")
        self.root.geometry("700x550") # Adjusted size slightly for CTk widgets

        self.translator_instance = None
        self.translation_thread = None
        # Use tk.StringVar, it's compatible with CTkEntry
        self.input_file_path = tk.StringVar()

        # --- GUI Elements ---

        # Configure grid layout
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(2, weight=1) # Log frame row expands

        # Frame for file selection
        file_frame = customtkinter.CTkFrame(root)
        file_frame.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="ew")
        file_frame.grid_columnconfigure(1, weight=1) # Make entry expand

        customtkinter.CTkLabel(file_frame, text="Input File:").grid(row=0, column=0, padx=(10, 5), pady=10, sticky="w")
        self.file_entry = customtkinter.CTkEntry(file_frame, textvariable=self.input_file_path, width=350, state='disabled') # CTk uses 'disabled'
        self.file_entry.grid(row=0, column=1, padx=(0, 5), pady=10, sticky="ew")
        self.browse_button = customtkinter.CTkButton(file_frame, text="Browse...", command=self.select_file, width=80)
        self.browse_button.grid(row=0, column=2, padx=(0, 10), pady=10, sticky="e")

        # Frame for controls
        control_frame = customtkinter.CTkFrame(root)
        control_frame.grid(row=1, column=0, padx=10, pady=5, sticky="ew")
        control_frame.grid_columnconfigure(2, weight=1) # Push progress label to the right

        self.start_button = customtkinter.CTkButton(control_frame, text="Start Translation", command=self.start_translation, state="disabled") # Use string state
        self.start_button.grid(row=0, column=0, padx=(10, 5), pady=10)
        self.stop_button = customtkinter.CTkButton(control_frame, text="Stop Translation", command=self.stop_translation, state="disabled") # Use string state
        self.stop_button.grid(row=0, column=1, padx=5, pady=10)
        self.progress_label = customtkinter.CTkLabel(control_frame, text="Idle")
        self.progress_label.grid(row=0, column=2, padx=(5, 10), pady=10, sticky="e")


        # Frame for logs
        log_frame = customtkinter.CTkFrame(root)
        log_frame.grid(row=2, column=0, padx=10, pady=(5, 10), sticky="nsew")
        log_frame.grid_rowconfigure(1, weight=1)    # Make textbox expand vertically
        log_frame.grid_columnconfigure(0, weight=1) # Make textbox expand horizontally


        customtkinter.CTkLabel(log_frame, text="Log Output:").grid(row=0, column=0, padx=10, pady=(10, 0), sticky="w")
        self.log_area = customtkinter.CTkTextbox(log_frame, wrap=tk.WORD, state='disabled') # Use CTkTextbox, state='disabled'
        self.log_area.grid(row=1, column=0, padx=10, pady=5, sticky="nsew")

    def select_file(self):
        # File dialog is still from tkinter
        file_path = filedialog.askopenfilename(
            title="Select Input Text File",
            filetypes=(("Text files", "*.txt"), ("All files", "*.*"))
        )
        if file_path:
            self.input_file_path.set(file_path)
            self.start_button.configure(state="normal") # Use .configure() and string state
            self.log_message(f"Selected file: {file_path}")

    def log_message(self, message):
        """Appends a message to the log area in a thread-safe way."""
        def _append():
            current_state = self.log_area.cget("state")
            self.log_area.configure(state="normal") # Use .configure()
            self.log_area.insert(tk.END, message + "\n")
            self.log_area.see(tk.END) # Scroll to the bottom
            self.log_area.configure(state=current_state) # Restore original state
        # Schedule the GUI update to run in the main thread
        if self.root.winfo_exists(): # Check if window still exists
            self.root.after(0, _append)

    def update_progress(self, message):
        """Updates the progress label in a thread-safe way."""
        def _update():
            self.progress_label.configure(text=message) # Use .configure()
        # Schedule the GUI update to run in the main thread
        if self.root.winfo_exists(): # Check if window still exists
            self.root.after(0, _update)


    def start_translation(self):
        file_path = self.input_file_path.get()
        if not file_path or not os.path.exists(file_path):
            self.log_message("Error: Please select a valid input file.")
            return

        self.log_message(f"Starting translation for: {file_path}")
        self.start_button.configure(state="disabled")   # Use .configure() and string state
        self.stop_button.configure(state="normal")     # Use .configure() and string state
        self.browse_button.configure(state="disabled")  # Use .configure() and string state
        self.progress_label.configure(text="Initializing...") # Use .configure()

        # Ensure the translator class can accept these callbacks
        try:
            self.translator_instance = AdvancedTranslator(
                log_callback=self.log_message,
                progress_callback=self.update_progress
            )
        except TypeError:
             self.log_message("Error: AdvancedTranslator cannot be initialized.")
             self.log_message("Ensure it accepts 'log_callback' and 'progress_callback'.")
             self._on_translation_complete() # Reset UI state
             return
        except Exception as e:
             self.log_message(f"Error initializing translator: {e}")
             self._on_translation_complete() # Reset UI state
             return


        # Run translation in a separate thread
        self.translation_thread = threading.Thread(
            target=self._run_translation_thread,
            args=(file_path,),
            daemon=True
        )
        self.translation_thread.start()

    def _run_translation_thread(self, file_path):
        """Target function for the translation thread."""
        try:
            # Check if translator instance exists and has the method
            if hasattr(self.translator_instance, 'execute_translation'):
                 self.translator_instance.execute_translation(file_path)
                 self.log_message("Translation process finished.")
            else:
                self.log_message("Error: Translator instance missing 'execute_translation' method.")

        except Exception as e:
            import traceback
            self.log_message(f"Error during translation thread: {e}")
            self.log_message(f"Traceback: {traceback.format_exc()}") # Log traceback for debugging
        finally:
            # Schedule GUI updates back to the main thread
            if self.root.winfo_exists(): # Check if window still exists
                 self.root.after(0, self._on_translation_complete)

    def _on_translation_complete(self):
        """Actions to perform in the main thread after translation finishes or stops."""
        self.start_button.configure(state="normal")     # Use .configure() and string state
        self.stop_button.configure(state="disabled")    # Use .configure() and string state
        self.browse_button.configure(state="normal")    # Use .configure() and string state
        self.progress_label.configure(text="Idle")     # Use .configure()
        self.translator_instance = None # Clear instance
        self.translation_thread = None

    def stop_translation(self):
        if self.translator_instance and hasattr(self.translator_instance, 'request_stop'):
            self.log_message("Sending stop request...")
            self.translator_instance.request_stop()
            self.stop_button.configure(state="disabled") # Prevent multiple clicks
        elif self.translation_thread and self.translation_thread.is_alive():
             self.log_message("Translator instance missing or stop method unavailable, but thread active.")
             # Cannot gracefully stop without the method, UI will reset when thread finishes/errors out.
             self.stop_button.configure(state="disabled") # Still disable button
        else:
            self.log_message("No active translation process to stop.")


if __name__ == "__main__":
    # Make sure api_call.py and the AdvancedTranslator class are correctly defined
    # before running this.
    try:
        # Attempt a dummy instantiation to catch initialization errors early
        # We won't use this instance, just check if the class is usable
        _ = AdvancedTranslator(log_callback=lambda x: None, progress_callback=lambda x: None)
    except NameError:
         print("Fatal Error: AdvancedTranslator class not found in api_call.py")
         sys.exit(1)
    except TypeError:
         print("Fatal Error: AdvancedTranslator class in api_call.py likely doesn't accept")
         print("             log_callback and progress_callback arguments in __init__.")
         sys.exit(1)
    except Exception as e:
         print(f"Fatal Error during initial AdvancedTranslator check: {e}")
         sys.exit(1)


    root = customtkinter.CTk() # Use CTk main window
    app = TranslatorGUI(root)
    root.mainloop()