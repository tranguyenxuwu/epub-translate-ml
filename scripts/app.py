import io
import json
import os
import queue
import threading
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Dict, Optional

from PIL import Image, ImageTk

import translate_xml
from epub_to_xml import EbookProcessor
from translate_xml import PROMPT_TEMPLATE, XMLTranslator
from xml_to_epub import create_epub_from_xml


class QueueStream(io.TextIOBase):
    """A stream-like object that pushes written text to a queue."""

    def __init__(self, output_queue: queue.Queue[str]):
        super().__init__()
        self.output_queue = output_queue

    def write(self, data: str) -> int:  # type: ignore[override]
        if not data:
            return 0
        self.output_queue.put(data)
        return len(data)

    def flush(self) -> None:  # type: ignore[override]
        return None


class EpubTranslatorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("EPUB Translator")
        self.root.geometry("1200x820")

        self.output_dir = Path("output")
        self.output_dir.mkdir(exist_ok=True)
        (self.output_dir / "images").mkdir(exist_ok=True)

        # --- Application state ---
        self.epub_path_var = tk.StringVar()
        self.xml_path: Optional[Path] = None
        self.translated_xml_path: Optional[Path] = None
        self.progress_file: Optional[Path] = None

        self.api_key_var = tk.StringVar(value=os.getenv("API_KEY", ""))
        self.model_options = [
            "deepseek/deepseek-r1-0528:free",
            "deepseek/deepseek-r1:free",
            "custom",
        ]
        self.model_var = tk.StringVar(value=self.model_options[0])
        self.custom_model_var = tk.StringVar()
        self.batch_size_var = tk.IntVar(value=70)

        self.translation_running = False
        self.active_translator: Optional[XMLTranslator] = None
        self.translation_thread: Optional[threading.Thread] = None
        self.log_queue: queue.Queue[str] = queue.Queue()

        self.progress_summary_var = tk.StringVar(value="No translation progress yet.")
        self.status_var = tk.StringVar(value="Ready.")

        self.final_epub_name_var = tk.StringVar(value="translated_output.epub")

        # Preview data containers
        self.preview_data: Dict[str, Dict] = {}
        self.preview_images: Dict[str, ImageTk.PhotoImage] = {}

        self._build_ui()
        self.refresh_file_state()
        self.root.after(200, self._process_log_queue)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        style = ttk.Style()
        style.configure("TLabel", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10))

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        workflow_frame = ttk.Frame(notebook)
        preview_frame = ttk.Frame(notebook)
        notebook.add(workflow_frame, text="Workflow")
        notebook.add(preview_frame, text="Preview")

        self._build_workflow_tab(workflow_frame)
        self._build_preview_tab(preview_frame)

        status_frame = ttk.Frame(self.root)
        status_frame.pack(fill=tk.X, padx=12, pady=(0, 12))
        ttk.Label(status_frame, textvariable=self.status_var).pack(side=tk.LEFT)

    def _build_workflow_tab(self, parent: tk.Widget) -> None:
        # Step 1 - Conversion
        step1 = ttk.LabelFrame(parent, text="Step 1 · Convert EPUB to XML")
        step1.pack(fill=tk.X, padx=8, pady=6)

        path_frame = ttk.Frame(step1)
        path_frame.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(path_frame, text="EPUB file:").pack(side=tk.LEFT)
        entry = ttk.Entry(path_frame, textvariable=self.epub_path_var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Button(path_frame, text="Browse…", command=self._choose_epub).pack(side=tk.LEFT)

        ttk.Button(
            step1,
            text="Convert to XML",
            command=self.start_conversion,
        ).pack(padx=8, pady=(0, 8), anchor=tk.W)

        # Step 2 - Translation settings
        step2 = ttk.LabelFrame(parent, text="Step 2 · Translate XML Content")
        step2.pack(fill=tk.BOTH, padx=8, pady=6)

        api_frame = ttk.Frame(step2)
        api_frame.pack(fill=tk.X, padx=8, pady=(8, 4))
        ttk.Label(api_frame, text="API key:").pack(side=tk.LEFT)
        api_entry = ttk.Entry(api_frame, textvariable=self.api_key_var, show="*")
        api_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Button(api_frame, text="Save", command=self._save_api_key).pack(side=tk.LEFT)

        options_frame = ttk.Frame(step2)
        options_frame.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(options_frame, text="Model:").grid(row=0, column=0, sticky=tk.W)
        model_combo = ttk.Combobox(
            options_frame,
            textvariable=self.model_var,
            values=self.model_options,
            state="readonly",
        )
        model_combo.grid(row=0, column=1, sticky=tk.W, padx=6)
        model_combo.bind("<<ComboboxSelected>>", lambda _: self._toggle_custom_model())

        ttk.Label(options_frame, text="Custom model:").grid(row=0, column=2, sticky=tk.W, padx=(16, 0))
        self.custom_model_entry = ttk.Entry(options_frame, textvariable=self.custom_model_var, width=32)
        self.custom_model_entry.grid(row=0, column=3, sticky=tk.W, padx=6)

        ttk.Label(options_frame, text="Batch size:").grid(row=0, column=4, sticky=tk.W, padx=(16, 0))
        batch_spin = ttk.Spinbox(options_frame, from_=10, to=200, increment=10, textvariable=self.batch_size_var, width=6)
        batch_spin.grid(row=0, column=5, sticky=tk.W, padx=6)

        self._toggle_custom_model()

        prompt_frame = ttk.LabelFrame(step2, text="Translation prompt")
        prompt_frame.pack(fill=tk.BOTH, padx=8, pady=6, expand=True)

        self.prompt_text = ScrolledText(prompt_frame, height=14, wrap=tk.WORD)
        self.prompt_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.prompt_text.insert("1.0", PROMPT_TEMPLATE)

        templates_frame = ttk.Frame(prompt_frame)
        templates_frame.pack(anchor=tk.W, padx=6, pady=(0, 6))
        ttk.Label(templates_frame, text="Quick templates:").pack(side=tk.LEFT)
        ttk.Button(
            templates_frame,
            text="Light novel",
            command=lambda: self._apply_prompt_template(PROMPT_TEMPLATE),
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            templates_frame,
            text="General",
            command=lambda: self._apply_prompt_template(self._general_prompt_template()),
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            templates_frame,
            text="Formal",
            command=lambda: self._apply_prompt_template(self._formal_prompt_template()),
        ).pack(side=tk.LEFT, padx=4)

        actions_frame = ttk.Frame(step2)
        actions_frame.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(actions_frame, text="Start translation", command=self.start_translation).pack(side=tk.LEFT)
        self.stop_button = ttk.Button(actions_frame, text="Stop", command=self.stop_translation, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=6)

        progress_frame = ttk.Frame(step2)
        progress_frame.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.progress_bar = ttk.Progressbar(progress_frame, orient=tk.HORIZONTAL, length=280, mode="determinate")
        self.progress_bar.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(progress_frame, textvariable=self.progress_summary_var).pack(side=tk.LEFT)

        # Step 3 - Final EPUB
        step3 = ttk.LabelFrame(parent, text="Step 3 · Create final EPUB")
        step3.pack(fill=tk.X, padx=8, pady=6)

        info_frame = ttk.Frame(step3)
        info_frame.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(info_frame, text="Output EPUB name:").pack(side=tk.LEFT)
        ttk.Entry(info_frame, textvariable=self.final_epub_name_var, width=40).pack(side=tk.LEFT, padx=6)
        ttk.Button(step3, text="Create EPUB", command=self.create_epub).pack(padx=8, pady=(0, 8), anchor=tk.W)

        # Log view
        log_frame = ttk.LabelFrame(parent, text="Activity log")
        log_frame.pack(fill=tk.BOTH, padx=8, pady=(6, 8), expand=True)

        self.log_text = ScrolledText(log_frame, height=12, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

    def _build_preview_tab(self, parent: tk.Widget) -> None:
        controls = ttk.Frame(parent)
        controls.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(controls, text="Load latest data", command=self.load_preview_data).pack(side=tk.LEFT)
        ttk.Button(controls, text="Refresh", command=self.refresh_file_state).pack(side=tk.LEFT, padx=6)

        paned = ttk.Panedwindow(parent, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        left_frame = ttk.Frame(paned)
        right_frame = ttk.Frame(paned)
        paned.add(left_frame, weight=1)
        paned.add(right_frame, weight=2)

        tree_scroll = ttk.Scrollbar(left_frame)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.preview_tree = ttk.Treeview(
            left_frame,
            columns=("Type", "Status"),
            show="tree headings",
            yscrollcommand=tree_scroll.set,
        )
        self.preview_tree.heading("#0", text="Content")
        self.preview_tree.heading("Type", text="Type")
        self.preview_tree.heading("Status", text="Status")
        self.preview_tree.column("#0", width=280)
        self.preview_tree.column("Type", width=100, anchor=tk.CENTER)
        self.preview_tree.column("Status", width=120, anchor=tk.CENTER)
        self.preview_tree.pack(fill=tk.BOTH, expand=True)
        tree_scroll.config(command=self.preview_tree.yview)
        self.preview_tree.bind("<<TreeviewSelect>>", self._on_preview_select)

        detail_notebook = ttk.Notebook(right_frame)
        detail_notebook.pack(fill=tk.BOTH, expand=True)

        text_frame = ttk.Frame(detail_notebook)
        image_frame = ttk.Frame(detail_notebook)
        detail_notebook.add(text_frame, text="Text details")
        detail_notebook.add(image_frame, text="Image preview")

        self.preview_title_var = tk.StringVar(value="Select an item to preview.")
        ttk.Label(text_frame, textvariable=self.preview_title_var, font=("Segoe UI", 11, "bold")).pack(
            anchor=tk.W, padx=8, pady=(8, 0)
        )

        text_split = ttk.Panedwindow(text_frame, orient=tk.VERTICAL)
        text_split.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        original_frame = ttk.LabelFrame(text_split, text="Original")
        translated_frame = ttk.LabelFrame(text_split, text="Translated")
        text_split.add(original_frame, weight=1)
        text_split.add(translated_frame, weight=1)

        self.preview_original_text = ScrolledText(original_frame, wrap=tk.WORD, height=12)
        self.preview_original_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.preview_translated_text = ScrolledText(translated_frame, wrap=tk.WORD, height=12)
        self.preview_translated_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        for widget in (self.preview_original_text, self.preview_translated_text):
            widget.configure(state=tk.DISABLED)

        image_frame_inner = ttk.Frame(image_frame)
        image_frame_inner.pack(fill=tk.BOTH, expand=True)
        self.preview_image_label = ttk.Label(image_frame_inner, text="No image selected.")
        self.preview_image_label.pack(padx=8, pady=8)

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------
    def _choose_epub(self) -> None:
        file_path = filedialog.askopenfilename(filetypes=[("EPUB files", "*.epub")])
        if file_path:
            self.epub_path_var.set(file_path)

    def _apply_prompt_template(self, template: str) -> None:
        self.prompt_text.delete("1.0", tk.END)
        self.prompt_text.insert("1.0", template)

    @staticmethod
    def _general_prompt_template() -> str:
        return (
            "Translate the following text into Vietnamese. Follow these instructions:\n"
            "1. Maintain the original meaning and tone\n"
            "2. Use natural, fluent Vietnamese\n"
            "3. Preserve formatting and structure\n"
            "4. Each input is in format id: text_to_translate\n"
            "5. Return as id: translated_text\n\n"
            "Content to translate:\n{content}"
        )

    @staticmethod
    def _formal_prompt_template() -> str:
        return (
            "Translate the following text into formal Vietnamese suitable for academic or professional contexts."
            " Follow these instructions:\n"
            "1. Use formal, polished language\n"
            "2. Maintain technical terminology accuracy\n"
            "3. Preserve original structure and meaning\n"
            "4. Each input is in format id: text_to_translate\n"
            "5. Return as id: translated_text\n\n"
            "Content to translate:\n{content}"
        )

    def _toggle_custom_model(self) -> None:
        if self.model_var.get() == "custom":
            self.custom_model_entry.configure(state=tk.NORMAL)
        else:
            self.custom_model_entry.configure(state=tk.DISABLED)

    def _save_api_key(self) -> None:
        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showwarning("API key", "Please enter a valid API key.")
            return
        os.environ["API_KEY"] = api_key
        self.append_log("API key saved to environment for current session.\n")
        messagebox.showinfo("API key", "API key saved for the current session.")

    def append_log(self, message: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, message)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _process_log_queue(self) -> None:
        while not self.log_queue.empty():
            message = self.log_queue.get()
            self.append_log(message)
        self.root.after(200, self._process_log_queue)

    def refresh_file_state(self) -> None:
        xml_candidates = [p for p in self.output_dir.glob("*.xml") if not p.name.endswith("_translated.xml")]
        self.xml_path = xml_candidates[0] if xml_candidates else None

        translated_candidates = list(self.output_dir.glob("*_translated.xml"))
        self.translated_xml_path = translated_candidates[0] if translated_candidates else None

        progress_candidates = list(self.output_dir.glob("*progress.json"))
        self.progress_file = progress_candidates[0] if progress_candidates else None

        if self.translated_xml_path:
            self.final_epub_name_var.set(f"{self.translated_xml_path.stem}.epub")

        if self.xml_path:
            self.append_log(f"XML file detected: {self.xml_path.name}\n")
        if self.translated_xml_path:
            self.append_log(f"Translated XML detected: {self.translated_xml_path.name}\n")

        self.update_progress_summary()

    def detect_previous_translations(self) -> Dict[str, object]:
        info: Dict[str, object] = {
            "has_progress": False,
            "has_translated_xml": False,
            "translated_count": 0,
            "total_count": 0,
            "completion_percentage": 0.0,
        }

        if self.progress_file and self.progress_file.exists():
            try:
                with open(self.progress_file, "r", encoding="utf-8") as f:
                    progress_data = json.load(f)
                info["has_progress"] = True
                info["translated_count"] = len(progress_data)
            except Exception as exc:  # pragma: no cover - defensive
                self.append_log(f"Failed to read progress file: {exc}\n")

        if self.xml_path and self.xml_path.exists():
            try:
                import xml.etree.ElementTree as ET

                tree = ET.parse(self.xml_path)
                root = tree.getroot()
                total_elements = 0

                for chapter in root.findall(".//chapter"):
                    if chapter.get("title"):
                        total_elements += 1
                    for elem in chapter:
                        if elem.tag == "paragraph" and elem.get("translate") == "yes":
                            total_elements += 1

                info["total_count"] = total_elements
                translated_count = info["translated_count"]  # type: ignore[assignment]
                if total_elements > 0:
                    percentage = (translated_count / total_elements) * 100
                    info["completion_percentage"] = percentage
            except Exception as exc:  # pragma: no cover - defensive
                self.append_log(f"Could not compute total elements: {exc}\n")

        if self.translated_xml_path and self.translated_xml_path.exists():
            info["has_translated_xml"] = True

        return info

    def update_progress_summary(self) -> None:
        info = self.detect_previous_translations()
        translated = info.get("translated_count", 0)
        total = info.get("total_count", 0)
        percentage = info.get("completion_percentage", 0.0)
        self.progress_bar["value"] = percentage
        if total:
            summary = f"Progress: {int(translated)}/{int(total)} elements ({percentage:.1f}%)"
        elif translated:
            summary = f"Translated elements: {int(translated)}"
        else:
            summary = "No translation progress yet."
        self.progress_summary_var.set(summary)

    # ------------------------------------------------------------------
    # EPUB conversion
    # ------------------------------------------------------------------
    def start_conversion(self) -> None:
        if self.translation_running:
            messagebox.showinfo("Conversion", "Translation is currently running. Please stop it before converting.")
            return

        epub_path = self.epub_path_var.get().strip()
        if not epub_path:
            messagebox.showwarning("Conversion", "Please choose an EPUB file to convert.")
            return

        if not Path(epub_path).exists():
            messagebox.showerror("Conversion", "The selected EPUB file does not exist.")
            return

        self.status_var.set("Converting EPUB…")
        self.append_log("Starting EPUB to XML conversion…\n")

        def worker() -> None:
            success = False
            error: Optional[str] = None
            try:
                processor = EbookProcessor(epub_path, str(self.output_dir))
                result = processor.run()
                if isinstance(result, dict):
                    success = bool(result.get("success"))
                else:
                    success = bool(result)
            except Exception as exc:
                error = str(exc)
            finally:
                self.root.after(0, lambda: self._on_conversion_finished(success, error))

        threading.Thread(target=worker, daemon=True).start()

    def _on_conversion_finished(self, success: bool, error: Optional[str]) -> None:
        if success:
            self.append_log("EPUB converted successfully.\n")
        else:
            self.append_log("EPUB conversion failed.\n")
        if error:
            messagebox.showerror("Conversion", f"An error occurred during conversion:\n{error}")
        elif success:
            messagebox.showinfo("Conversion", "EPUB converted to XML successfully.")
        self.status_var.set("Ready.")
        self.refresh_file_state()

    # ------------------------------------------------------------------
    # Translation handling
    # ------------------------------------------------------------------
    def start_translation(self) -> None:
        if self.translation_running:
            messagebox.showinfo("Translation", "A translation is already in progress.")
            return

        if not self.xml_path or not self.xml_path.exists():
            messagebox.showwarning("Translation", "No XML file found. Please convert an EPUB first.")
            return

        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showwarning("Translation", "Please provide an API key before starting the translation.")
            return

        os.environ["API_KEY"] = api_key

        try:
            translator = XMLTranslator(str(self.xml_path))
        except ValueError as exc:
            messagebox.showerror("Translation", str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            messagebox.showerror("Translation", f"Failed to initialise translator: {exc}")
            return

        prompt = self.prompt_text.get("1.0", tk.END).strip()
        if prompt:
            translator.set_custom_prompt(prompt)

        model_choice = self.model_var.get()
        if model_choice == "custom":
            custom_model = self.custom_model_var.get().strip()
            if not custom_model:
                messagebox.showwarning("Translation", "Please enter a custom model name.")
                return
            translator.set_model(custom_model)
        else:
            translator.set_model(model_choice)

        try:
            batch_size = int(self.batch_size_var.get())
            translator.set_batch_size(batch_size)
        except ValueError:
            messagebox.showwarning("Translation", "Invalid batch size. Please enter a number.")
            return

        self.translation_running = True
        self.active_translator = translator
        translate_xml.stop_flag = False
        self.status_var.set("Translating…")
        self.append_log("Starting translation…\n")
        self.stop_button.configure(state=tk.NORMAL)

        def worker() -> None:
            success = False
            error: Optional[str] = None
            stream = QueueStream(self.log_queue)
            try:
                with redirect_stdout(stream), redirect_stderr(stream):
                    translator.run()
                info = translator.get_progress_info()
                success = info.get("completion_percentage", 0) >= 99.9
            except Exception as exc:  # pragma: no cover - defensive
                error = str(exc)
            finally:
                self.root.after(0, lambda: self._on_translation_finished(success, error))

        self.translation_thread = threading.Thread(target=worker, daemon=True)
        self.translation_thread.start()
        self.root.after(1000, self._poll_translation_progress)

    def _poll_translation_progress(self) -> None:
        if not self.translation_running or not self.active_translator:
            return
        try:
            info = self.active_translator.get_progress_info()
            percentage = info.get("completion_percentage", 0)
            translated = info.get("translated_elements", 0)
            total = info.get("total_elements", 0)
            self.progress_bar["value"] = percentage
            self.progress_summary_var.set(
                f"Progress: {translated}/{total} elements ({percentage:.1f}%)"
            )
        except Exception as exc:  # pragma: no cover - defensive
            self.append_log(f"Could not update progress: {exc}\n")
        finally:
            if self.translation_running:
                self.root.after(1500, self._poll_translation_progress)

    def _on_translation_finished(self, success: bool, error: Optional[str]) -> None:
        self.translation_running = False
        self.stop_button.configure(state=tk.DISABLED)
        self.status_var.set("Ready.")
        self.active_translator = None
        self.translation_thread = None
        self.refresh_file_state()

        if error:
            messagebox.showerror("Translation", f"An error occurred during translation:\n{error}")
            return

        if translate_xml.stop_flag:
            messagebox.showinfo(
                "Translation",
                "Translation stopped by user. Progress has been saved and can be resumed.",
            )
        elif success:
            messagebox.showinfo("Translation", "Translation completed successfully.")
        else:
            messagebox.showinfo(
                "Translation",
                "Translation finished with partial progress. You can run it again to continue.",
            )

    def stop_translation(self) -> None:
        if self.translation_running and self.active_translator:
            self.active_translator.stop_translation()
            translate_xml.stop_flag = True
            self.append_log("Stop signal sent. Translation will pause after current batch.\n")
            self.stop_button.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------
    # EPUB creation
    # ------------------------------------------------------------------
    def create_epub(self) -> None:
        if not self.translated_xml_path or not self.translated_xml_path.exists():
            messagebox.showwarning("Create EPUB", "No translated XML file found. Please complete the translation first.")
            return

        target_name = self.final_epub_name_var.get().strip() or f"{self.translated_xml_path.stem}.epub"
        try:
            create_epub_from_xml(
                str(self.translated_xml_path),
                target_name,
                str(self.output_dir),
            )
            self.append_log(f"Created EPUB: {target_name}\n")
            messagebox.showinfo(
                "Create EPUB",
                f"EPUB file '{target_name}' created in {self.output_dir.resolve()}",
            )
        except Exception as exc:
            messagebox.showerror("Create EPUB", f"Failed to create EPUB: {exc}")

    # ------------------------------------------------------------------
    # Preview handling
    # ------------------------------------------------------------------
    def load_preview_data(self) -> None:
        if not self.xml_path or not self.xml_path.exists():
            messagebox.showwarning("Preview", "No XML file found. Please run the conversion first.")
            return

        import xml.etree.ElementTree as ET

        try:
            original_tree = ET.parse(self.xml_path)
            original_root = original_tree.getroot()
        except Exception as exc:
            messagebox.showerror("Preview", f"Failed to load XML file: {exc}")
            return

        translation_map: Dict[str, str] = {}

        if self.progress_file and self.progress_file.exists():
            try:
                with open(self.progress_file, "r", encoding="utf-8") as f:
                    translation_map.update(json.load(f))
            except Exception as exc:
                self.append_log(f"Could not read progress file for preview: {exc}\n")

        if self.translated_xml_path and self.translated_xml_path.exists():
            try:
                translated_tree = ET.parse(self.translated_xml_path)
                translated_root = translated_tree.getroot()
                for chapter in translated_root.findall(".//chapter"):
                    chapter_id = chapter.get("id")
                    if chapter_id:
                        translation_map[f"{chapter_id}_title"] = chapter.get("title", "")
                    for para in chapter.findall(".//paragraph[@translate='yes']"):
                        para_id = para.get("id")
                        text_elem = para.find("text")
                        if para_id and text_elem is not None and text_elem.text:
                            translation_map[para_id] = text_elem.text
            except Exception as exc:
                self.append_log(f"Could not read translated XML for preview: {exc}\n")

        self.preview_tree.delete(*self.preview_tree.get_children())
        self.preview_data.clear()
        self.preview_images.clear()

        for chapter in original_root.findall("chapter"):
            chapter_id = chapter.get("id", f"chapter_{len(self.preview_data)+1}")
            chapter_title = chapter.get("title", "Untitled chapter")
            translated_title = translation_map.get(f"{chapter_id}_title", chapter_title)

            chapter_node = self.preview_tree.insert(
                "",
                tk.END,
                iid=chapter_id,
                text=translated_title or chapter_title,
                values=("Chapter", "Translated" if translated_title != chapter_title else "Original"),
            )

            chapter_data: Dict[str, object] = {
                "type": "chapter",
                "original_title": chapter_title,
                "translated_title": translated_title,
            }
            self.preview_data[chapter_id] = chapter_data

            image_index = 0
            for item in chapter:
                if item.tag == "paragraph":
                    para_id = item.get("id", f"{chapter_id}_p{len(self.preview_data)}")
                    role = item.get("role", "")
                    text_elem = item.find("text")
                    original_text = text_elem.text if text_elem is not None else ""
                    translated_text = translation_map.get(para_id, original_text)
                    status = "Translated" if translated_text != original_text else "Pending"

                    self.preview_tree.insert(
                        chapter_node,
                        tk.END,
                        iid=para_id,
                        text=(translated_text or original_text)[:80],
                        values=("Paragraph", status),
                    )

                    self.preview_data[para_id] = {
                        "type": "paragraph",
                        "role": role,
                        "original": original_text,
                        "translated": translated_text,
                        "chapter": chapter_id,
                    }

                elif item.tag == "image":
                    image_index += 1
                    img_id = item.get("id", f"{chapter_id}_img{image_index}")
                    img_src = item.get("src", "")
                    img_alt = item.get("alt", "")
                    status = "Available" if img_src else "Missing"

                    self.preview_tree.insert(
                        chapter_node,
                        tk.END,
                        iid=img_id,
                        text=img_alt or img_src or "Image",
                        values=("Image", status),
                    )

                    absolute_path = (self.xml_path.parent / img_src).resolve()
                    self.preview_data[img_id] = {
                        "type": "image",
                        "src": img_src,
                        "alt": img_alt,
                        "path": absolute_path,
                    }

        self.preview_title_var.set("Select an item to preview.")
        self._clear_preview_text()
        self.preview_image_label.configure(text="No image selected.", image="")

    def _on_preview_select(self, _event: object) -> None:
        selection = self.preview_tree.selection()
        if not selection:
            return
        item_id = selection[0]
        data = self.preview_data.get(item_id)
        if not data:
            return

        item_type = data.get("type")
        if item_type == "chapter":
            self.preview_title_var.set("Chapter details")
            self._set_preview_text(
                original=data.get("original_title", ""),
                translated=data.get("translated_title", ""),
            )
            self.preview_image_label.configure(text="No image selected.", image="")
        elif item_type == "paragraph":
            role = data.get("role")
            title = "Paragraph"
            if role:
                title += f" ({role})"
            self.preview_title_var.set(title)
            self._set_preview_text(
                original=data.get("original", ""),
                translated=data.get("translated", ""),
            )
            self.preview_image_label.configure(text="No image selected.", image="")
        elif item_type == "image":
            self.preview_title_var.set(data.get("alt") or "Image")
            self._clear_preview_text()
            self._display_preview_image(data)

    def _set_preview_text(self, original: str, translated: str) -> None:
        self.preview_original_text.configure(state=tk.NORMAL)
        self.preview_original_text.delete("1.0", tk.END)
        self.preview_original_text.insert("1.0", original)
        self.preview_original_text.configure(state=tk.DISABLED)

        self.preview_translated_text.configure(state=tk.NORMAL)
        self.preview_translated_text.delete("1.0", tk.END)
        self.preview_translated_text.insert("1.0", translated)
        self.preview_translated_text.configure(state=tk.DISABLED)

    def _clear_preview_text(self) -> None:
        self.preview_original_text.configure(state=tk.NORMAL)
        self.preview_original_text.delete("1.0", tk.END)
        self.preview_original_text.configure(state=tk.DISABLED)

        self.preview_translated_text.configure(state=tk.NORMAL)
        self.preview_translated_text.delete("1.0", tk.END)
        self.preview_translated_text.configure(state=tk.DISABLED)

    def _display_preview_image(self, data: Dict[str, object]) -> None:
        path = data.get("path")
        if not path or not Path(path).exists():
            self.preview_image_label.configure(text="Image file not found.", image="")
            return

        try:
            image = Image.open(Path(path))
            max_size = (560, 560)
            image.thumbnail(max_size, Image.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            self.preview_images[str(path)] = photo  # Keep reference
            self.preview_image_label.configure(image=photo, text="")
        except Exception as exc:
            self.preview_image_label.configure(text=f"Unable to load image: {exc}", image="")


def main() -> None:
    root = tk.Tk()
    app = EpubTranslatorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
