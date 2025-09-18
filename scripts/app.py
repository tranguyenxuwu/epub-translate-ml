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
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageTk
import sv_ttk

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

        try:
            sv_ttk.set_theme("light")
        except Exception as exc:  # pragma: no cover - defensive
            print(f"Unable to apply Sun Valley theme: {exc}")

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
        self.recent_activity: List[str] = []
        self.max_activity_messages = 200
        self._log_buffer = ""

        self.progress_summary_var = tk.StringVar(value="No translation progress yet.")
        self.status_var = tk.StringVar(value="Ready.")

        self.final_epub_name_var = tk.StringVar(value="translated_output.epub")

        # Preview data containers
        self.preview_items: List[Dict[str, object]] = []
        self.preview_image_cache: Dict[str, ImageTk.PhotoImage] = {}
        self.preview_rendered_widgets: List[Dict[str, object]] = []
        self.preview_removed_stack: List[Tuple[int, Dict[str, object], int]] = []
        self.preview_next_index = 0
        self.preview_first_index = 0
        self.preview_spacer_height = 0
        self.preview_chunk_size = 12
        self.preview_keep_items = 60
        self.preview_wraplength = 760
        self.preview_status_var = tk.StringVar(value="Load preview data to see content.")

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
        style.configure("Heading.TLabel", font=("Segoe UI Semibold", 14))
        style.configure("ParagraphText.TLabel", font=("Segoe UI", 11))
        style.configure("OriginalText.TLabel", font=("Segoe UI", 10, "italic"))
        style.configure("MetaInfo.TLabel", font=("Segoe UI", 9))
        style.configure("StatusBadge.TLabel", font=("Segoe UI Semibold", 9))

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

        activity_frame = ttk.LabelFrame(parent, text="Recent updates")
        activity_frame.pack(fill=tk.BOTH, padx=8, pady=(6, 12), expand=True)

        self.activity_list = tk.Listbox(
            activity_frame,
            height=10,
            activestyle="none",
            font=("Segoe UI", 10),
            exportselection=False,
        )
        self.activity_list.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.activity_list.configure(borderwidth=0, highlightthickness=0)

    def _build_preview_tab(self, parent: tk.Widget) -> None:
        controls = ttk.Frame(parent)
        controls.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(controls, text="Load latest data", command=self.load_preview_data).pack(side=tk.LEFT)
        ttk.Button(controls, text="Refresh", command=self.refresh_file_state).pack(side=tk.LEFT, padx=6)
        ttk.Label(controls, textvariable=self.preview_status_var, style="MetaInfo.TLabel").pack(
            side=tk.LEFT, padx=12
        )

        container = ttk.Frame(parent)
        container.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self.preview_canvas = tk.Canvas(container, highlightthickness=0)
        self.preview_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        preview_scroll = ttk.Scrollbar(container, orient=tk.VERTICAL, command=self._on_preview_scroll)
        preview_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.preview_canvas.configure(yscrollcommand=preview_scroll.set)

        self.preview_inner = ttk.Frame(self.preview_canvas)
        self.preview_window = self.preview_canvas.create_window((0, 0), window=self.preview_inner, anchor="nw")

        self.preview_inner.bind(
            "<Configure>", lambda _event: self.preview_canvas.configure(scrollregion=self.preview_canvas.bbox("all"))
        )
        self.preview_canvas.bind("<Configure>", self._on_preview_canvas_configure)

        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.preview_canvas.bind(sequence, self._on_preview_mousewheel)
            self.preview_inner.bind(sequence, self._on_preview_mousewheel)

        self.preview_spacer_top = ttk.Frame(self.preview_inner, height=0)
        self.preview_spacer_top.pack(fill=tk.X)
        self.preview_placeholder = ttk.Label(
            self.preview_inner,
            textvariable=self.preview_status_var,
            style="MetaInfo.TLabel",
            padding=(24, 32),
            justify=tk.CENTER,
            anchor="center",
            wraplength=640,
        )
        self.preview_placeholder.pack(fill=tk.BOTH, expand=True)

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
        normalised = message.replace("\r", "\n")
        self._log_buffer += normalised

        while "\n" in self._log_buffer:
            line, self._log_buffer = self._log_buffer.split("\n", 1)
            cleaned = line.rstrip()
            if cleaned.strip():
                self._add_activity_line(cleaned)

    def _process_log_queue(self) -> None:
        while not self.log_queue.empty():
            message = self.log_queue.get()
            self.append_log(message)
        self.root.after(200, self._process_log_queue)

    def _add_activity_line(self, line: str) -> None:
        if not line:
            return

        normalized = line.lstrip()
        if normalized.startswith("API Call Time:") and self.recent_activity:
            last = self.recent_activity[-1]
            if last.lstrip().startswith("API Call Time:"):
                self.recent_activity[-1] = line
                self.activity_list.delete(tk.END)
                self.activity_list.insert(tk.END, line)
                self.activity_list.see(tk.END)
                return

        self.recent_activity.append(line)
        if len(self.recent_activity) > self.max_activity_messages:
            self.recent_activity = self.recent_activity[-self.max_activity_messages :]
            self.activity_list.delete(0, tk.END)
            for entry in self.recent_activity:
                self.activity_list.insert(tk.END, entry)
        else:
            self.activity_list.insert(tk.END, line)

        self.activity_list.see(tk.END)

    def _flush_log_buffer(self) -> None:
        pending = self._log_buffer.rstrip()
        if pending.strip():
            self._add_activity_line(pending)
        self._log_buffer = ""

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
        translator = self.active_translator
        self.translation_running = False
        self.stop_button.configure(state=tk.DISABLED)
        self.status_var.set("Ready.")
        self.active_translator = None
        self.translation_thread = None
        self.refresh_file_state()
        self._flush_log_buffer()

        if error:
            messagebox.showerror("Translation", f"An error occurred during translation:\n{error}")
            return

        rate_limit_triggered = bool(getattr(translator, "rate_limit_stop_triggered", False))

        if rate_limit_triggered:
            messagebox.showwarning(
                "Translation",
                "Translation stopped after repeated HTTP 429 responses. Please wait before retrying.",
            )
        elif translate_xml.stop_flag:
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

        self.preview_items = []
        self.preview_image_cache.clear()

        for chapter in original_root.findall("chapter"):
            chapter_id = chapter.get("id") or f"chapter_{len(self.preview_items) + 1}"
            chapter_title = chapter.get("title", "Untitled chapter")
            translated_title = translation_map.get(f"{chapter_id}_title", chapter_title)

            self.preview_items.append(
                {
                    "type": "chapter",
                    "id": chapter_id,
                    "original_title": chapter_title,
                    "translated_title": translated_title,
                }
            )

            image_index = 0
            for item in chapter:
                if item.tag == "paragraph" and item.get("translate") == "yes":
                    para_id = item.get("id")
                    if not para_id:
                        continue
                    role = item.get("role", "")
                    text_elem = item.find("text")
                    original_text = text_elem.text if text_elem is not None else ""
                    translated_text = translation_map.get(para_id, original_text)
                    self.preview_items.append(
                        {
                            "type": "paragraph",
                            "id": para_id,
                            "role": role,
                            "original": original_text,
                            "translated": translated_text,
                            "chapter": chapter_id,
                        }
                    )
                elif item.tag == "image":
                    image_index += 1
                    img_id = item.get("id") or f"{chapter_id}_img{image_index}"
                    img_src = item.get("src", "")
                    img_alt = item.get("alt", "")
                    image_path = (self.xml_path.parent / img_src).resolve() if img_src else None
                    self.preview_items.append(
                        {
                            "type": "image",
                            "id": img_id,
                            "alt": img_alt,
                            "path": str(image_path) if image_path else "",
                            "source": img_src,
                            "chapter": chapter_id,
                        }
                    )

        total_blocks = len(self.preview_items)
        if total_blocks:
            self.preview_status_var.set(
                f"Loaded {total_blocks} content blocks. Scroll to review translations."
            )
        else:
            self.preview_status_var.set("No previewable content found in the XML file.")

        self._reset_preview_view()
        self.append_log("Preview data refreshed.\n")

    def _reset_preview_view(self) -> None:
        for record in self.preview_rendered_widgets:
            record["widget"].destroy()
        self.preview_rendered_widgets.clear()
        self.preview_removed_stack.clear()
        self.preview_next_index = 0
        self.preview_first_index = 0
        self.preview_spacer_height = 0
        self.preview_spacer_top.configure(height=0)

        if self.preview_items:
            if self.preview_placeholder.winfo_manager():
                self.preview_placeholder.pack_forget()
            self.preview_canvas.yview_moveto(0)
            self._load_next_preview_chunk(initial=True)
            self.root.after(100, self._check_preview_load)
        else:
            if not self.preview_placeholder.winfo_manager():
                self.preview_placeholder.pack(fill=tk.BOTH, expand=True)
            self.preview_canvas.yview_moveto(0)

    def _load_next_preview_chunk(self, initial: bool = False) -> None:
        if self.preview_next_index >= len(self.preview_items):
            return

        end_index = min(self.preview_next_index + self.preview_chunk_size, len(self.preview_items))

        for index in range(self.preview_next_index, end_index):
            item = self.preview_items[index]
            widget, height = self._create_preview_widget(index, item, before_widget=None)
            record = {"index": index, "widget": widget, "item": item, "height": height}
            self.preview_rendered_widgets.append(record)

        self.preview_next_index = end_index

        if not initial:
            self._trim_preview_if_needed()

    def _trim_preview_if_needed(self) -> None:
        if len(self.preview_rendered_widgets) <= self.preview_keep_items:
            return

        while len(self.preview_rendered_widgets) > self.preview_keep_items:
            record = self.preview_rendered_widgets.pop(0)
            height = record.get("height") or record["widget"].winfo_height()
            record["widget"].destroy()
            self.preview_removed_stack.append((record["index"], record["item"], height))
            self.preview_first_index = record["index"] + 1
            self.preview_spacer_height += height
            self.preview_spacer_top.configure(height=self.preview_spacer_height)

    def _restore_previous_chunk(self) -> None:
        if not self.preview_removed_stack:
            return

        to_restore: List[Tuple[int, Dict[str, object], int]] = []
        while self.preview_removed_stack and len(to_restore) < self.preview_chunk_size:
            to_restore.append(self.preview_removed_stack.pop())

        to_restore.reverse()
        anchor_widget = self.preview_rendered_widgets[0]["widget"] if self.preview_rendered_widgets else None

        for index, item, stored_height in to_restore:
            widget, height = self._create_preview_widget(index, item, before_widget=anchor_widget)
            record = {"index": index, "widget": widget, "item": item, "height": height}
            self.preview_rendered_widgets.insert(0, record)
            anchor_widget = widget
            self.preview_first_index = index
            self.preview_spacer_height = max(0, self.preview_spacer_height - stored_height)

        self.preview_spacer_top.configure(height=self.preview_spacer_height)

    def _create_preview_widget(
        self, index: int, item: Dict[str, object], before_widget: Optional[tk.Widget] = None
    ) -> Tuple[tk.Widget, int]:
        if self.preview_placeholder.winfo_manager():
            self.preview_placeholder.pack_forget()

        frame = ttk.Frame(self.preview_inner)

        if item["type"] == "chapter":
            title = item.get("translated_title") or item.get("original_title", "")
            original_title = item.get("original_title", "")
            ttk.Label(frame, text=title, style="Heading.TLabel", wraplength=self.preview_wraplength).pack(
                anchor=tk.W, pady=(0, 2)
            )
            if title != original_title and original_title:
                ttk.Label(frame, text=original_title, style="OriginalText.TLabel", wraplength=self.preview_wraplength).pack(
                    anchor=tk.W
                )
        elif item["type"] == "paragraph":
            translated = item.get("translated", "") or ""
            original = item.get("original", "") or ""
            status = "Translated" if translated and translated != original else "Pending translation"
            ttk.Label(frame, text=translated or original, style="ParagraphText.TLabel", wraplength=self.preview_wraplength, justify=tk.LEFT).pack(anchor=tk.W)
            if original and translated and translated != original:
                ttk.Label(
                    frame,
                    text=original,
                    style="OriginalText.TLabel",
                    wraplength=self.preview_wraplength,
                    justify=tk.LEFT,
                ).pack(anchor=tk.W, pady=(4, 0))
            ttk.Label(frame, text=status, style="StatusBadge.TLabel").pack(anchor=tk.W, pady=(4, 0))
        elif item["type"] == "image":
            path_str = item.get("path") or ""
            alt_text = item.get("alt") or item.get("source") or "Image"
            if path_str and Path(path_str).exists():
                key = str(Path(path_str).resolve())
                photo = self.preview_image_cache.get(key)
                if not photo:
                    try:
                        image = Image.open(key)
                        max_size = (min(720, self.preview_wraplength), 720)
                        image.thumbnail(max_size, Image.LANCZOS)
                        photo = ImageTk.PhotoImage(image)
                        self.preview_image_cache[key] = photo
                    except Exception as exc:  # pragma: no cover - defensive
                        ttk.Label(
                            frame,
                            text=f"Unable to load image: {exc}",
                            style="MetaInfo.TLabel",
                            wraplength=self.preview_wraplength,
                        ).pack(anchor=tk.W)
                        photo = None
                if photo:
                    label = ttk.Label(frame, image=photo)
                    label.image = photo  # type: ignore[attr-defined]
                    label.pack(anchor=tk.CENTER, pady=(0, 4))
            else:
                ttk.Label(
                    frame,
                    text="Image file not found.",
                    style="MetaInfo.TLabel",
                    wraplength=self.preview_wraplength,
                ).pack(anchor=tk.W)

            ttk.Label(
                frame,
                text=alt_text,
                style="MetaInfo.TLabel",
                wraplength=self.preview_wraplength,
            ).pack(anchor=tk.W)

        padding = (24, 12) if item["type"] == "chapter" else (24, 6)
        if before_widget is not None:
            frame.pack(fill=tk.X, padx=padding[0], pady=(padding[1], padding[1]), before=before_widget)
        else:
            frame.pack(fill=tk.X, padx=padding[0], pady=(padding[1], padding[1]))

        frame.update_idletasks()
        height = frame.winfo_height() or frame.winfo_reqheight()
        return frame, int(height)

    def _check_preview_load(self) -> None:
        if not self.preview_items:
            return

        bbox = self.preview_canvas.bbox("all")
        if not bbox:
            return

        bottom_visible = self.preview_canvas.canvasy(self.preview_canvas.winfo_height())
        if bottom_visible >= bbox[3] - 600:
            self._load_next_preview_chunk()

        top_visible = self.preview_canvas.canvasy(0)
        if top_visible <= 20:
            self._restore_previous_chunk()

    def _on_preview_canvas_configure(self, event: tk.Event) -> None:
        self.preview_canvas.itemconfigure(self.preview_window, width=event.width)
        self.preview_wraplength = max(320, event.width - 80)

    def _on_preview_mousewheel(self, event: tk.Event) -> str:
        if event.delta:
            self.preview_canvas.yview_scroll(int(-event.delta / 120), "units")
        elif getattr(event, "num", None) == 4:
            self.preview_canvas.yview_scroll(-3, "units")
        elif getattr(event, "num", None) == 5:
            self.preview_canvas.yview_scroll(3, "units")

        self._check_preview_load()
        return "break"

    def _on_preview_scroll(self, *args: object) -> None:
        self.preview_canvas.yview(*args)
        self._check_preview_load()


def main() -> None:
    root = tk.Tk()
    app = EpubTranslatorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
