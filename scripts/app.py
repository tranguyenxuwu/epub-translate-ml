import io
import json
import math
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

try:
    import sv_ttk
except ImportError:
    sv_ttk = None

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
        self._apply_theme()

        self.base_output_dir = Path("output")
        self.base_output_dir.mkdir(exist_ok=True)
        self.output_dir = self.base_output_dir
        self._ensure_output_subdirs(self.output_dir)

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
        self.request_queue: queue.Queue[Dict[str, object]] = queue.Queue()
        self.request_items: Dict[int, Dict[str, object]] = {}

        self.progress_summary_var = tk.StringVar(value="No translation progress yet.")
        self.status_var = tk.StringVar(value="Ready.")

        self.final_epub_name_var = tk.StringVar(value="translated_output.epub")
        self.metadata_title_var = tk.StringVar()
        self.metadata_author_var = tk.StringVar()
        self.metadata_language_var = tk.StringVar(value="vi")
        self.metadata_identifier_var = tk.StringVar()
        self.metadata_publisher_var = tk.StringVar()

        # Preview reader state
        self.reader_paragraphs: list[Dict[str, object]] = []
        self.reader_chapter_ranges: list[tuple[int, int]] = []
        self.reader_chunk_frames: Dict[int, tk.Widget] = {}
        self.reader_chunk_widgets: Dict[int, list[tk.Widget]] = {}
        self.reader_image_cache: Dict[int, list[ImageTk.PhotoImage]] = {}
        self.reader_loaded_chunks: list[int] = []
        self.reader_chunk_size = 1
        self.reader_max_loaded_chunks = 1
        self.reader_total_chunks = 0
        self.reader_wraplength = 900
        self.reader_placeholder_label: Optional[ttk.Label] = None
        self.reader_loading = False
        self.reader_load_token: Optional[object] = None

        self._build_ui()
        self.refresh_file_state()
        self.root.after(200, self._process_log_queue)
        self.root.after(250, self._process_request_queue)
    def _apply_theme(self) -> None:
        if sv_ttk:
            try:
                sv_ttk.set_theme("dark")
            except Exception as exc:  # pragma: no cover - defensive
                print(f"Failed to apply Sun Valley theme: {exc}")

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
        layout = ttk.Panedwindow(parent, orient=tk.HORIZONTAL)
        layout.pack(fill=tk.BOTH, expand=True)

        workflow_frame = ttk.Frame(layout)
        history_frame = ttk.Frame(layout)
        layout.add(workflow_frame, weight=2)
        layout.add(history_frame, weight=1)

        # Step 1 - Conversion
        step1 = ttk.LabelFrame(workflow_frame, text="Step 1 - Convert EPUB to XML")
        step1.pack(fill=tk.X, padx=8, pady=6)

        path_frame = ttk.Frame(step1)
        path_frame.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(path_frame, text="EPUB file:").pack(side=tk.LEFT)
        entry = ttk.Entry(path_frame, textvariable=self.epub_path_var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Button(path_frame, text="Browse...", command=self._choose_epub).pack(side=tk.LEFT)

        ttk.Button(
            step1,
            text="Convert to XML",
            command=self.start_conversion,
        ).pack(padx=8, pady=(0, 8), anchor=tk.W)

        # Step 2 - Translation settings
        step2 = ttk.LabelFrame(workflow_frame, text="Step 2 - Translate XML Content")
        step2.pack(fill=tk.BOTH, padx=8, pady=6, expand=True)

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
        ttk.Button(
            templates_frame,
            text="Save prompt...",
            command=self._save_prompt_to_file,
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
        step3 = ttk.LabelFrame(workflow_frame, text="Step 3 - Create final EPUB")
        step3.pack(fill=tk.X, padx=8, pady=6)

        info_frame = ttk.Frame(step3)
        info_frame.pack(fill=tk.X, padx=8, pady=6)
        info_frame.columnconfigure(1, weight=1)
        ttk.Label(info_frame, text="Output EPUB name:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(info_frame, textvariable=self.final_epub_name_var).grid(row=0, column=1, sticky=tk.EW, padx=(6, 0))

        metadata_frame = ttk.LabelFrame(step3, text="EPUB metadata")
        metadata_frame.pack(fill=tk.X, padx=8, pady=(0, 8))
        metadata_frame.columnconfigure(1, weight=1)

        ttk.Label(metadata_frame, text="Title:").grid(row=0, column=0, sticky=tk.W, pady=2)
        ttk.Entry(metadata_frame, textvariable=self.metadata_title_var).grid(row=0, column=1, sticky=tk.EW, padx=(6, 0), pady=2)
        ttk.Label(metadata_frame, text="Author:").grid(row=1, column=0, sticky=tk.W, pady=2)
        ttk.Entry(metadata_frame, textvariable=self.metadata_author_var).grid(row=1, column=1, sticky=tk.EW, padx=(6, 0), pady=2)
        ttk.Label(metadata_frame, text="Language:").grid(row=2, column=0, sticky=tk.W, pady=2)
        ttk.Entry(metadata_frame, textvariable=self.metadata_language_var, width=10).grid(row=2, column=1, sticky=tk.W, padx=(6, 0), pady=2)
        ttk.Label(metadata_frame, text="Identifier:").grid(row=3, column=0, sticky=tk.W, pady=2)
        ttk.Entry(metadata_frame, textvariable=self.metadata_identifier_var).grid(row=3, column=1, sticky=tk.EW, padx=(6, 0), pady=2)
        ttk.Label(metadata_frame, text="Publisher:").grid(row=4, column=0, sticky=tk.W, pady=2)
        ttk.Entry(metadata_frame, textvariable=self.metadata_publisher_var).grid(row=4, column=1, sticky=tk.EW, padx=(6, 0), pady=2)

        ttk.Button(step3, text="Create EPUB", command=self.create_epub).pack(padx=8, pady=(0, 8), anchor=tk.W)

        # Request history column
        history_frame.columnconfigure(0, weight=1)
        history_frame.rowconfigure(0, weight=1)
        output_frame = ttk.LabelFrame(history_frame, text="Output history")
        output_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        request_scroll = ttk.Scrollbar(output_frame, orient=tk.VERTICAL)
        self.request_tree = ttk.Treeview(
            output_frame,
            columns=("Status", "Details"),
            show="tree headings",
            height=24,
            yscrollcommand=request_scroll.set,
            selectmode="browse",
        )
        self.request_tree.heading("#0", text="Request")
        self.request_tree.heading("Status", text="Status")
        self.request_tree.heading("Details", text="Details")
        self.request_tree.column("#0", width=140, anchor=tk.W)
        self.request_tree.column("Status", width=110, anchor=tk.CENTER)
        self.request_tree.column("Details", width=320, anchor=tk.W)
        self.request_tree.grid(row=0, column=0, sticky="nsew")
        request_scroll.grid(row=0, column=1, sticky="ns")
        output_frame.columnconfigure(0, weight=1)
        output_frame.rowconfigure(0, weight=1)
        request_scroll.configure(command=self.request_tree.yview)
        self.request_tree.tag_configure("pending", background="#CCE4FF")
        self.request_tree.tag_configure("finished", background="#CCF5D3")
        self.request_tree.tag_configure("429", background="#F8CACA")
        self.request_tree.tag_configure("error", background="#FDE2E2")
    def _build_preview_tab(self, parent: tk.Widget) -> None:
        controls = ttk.Frame(parent)
        controls.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(controls, text="Load latest data", command=self.load_preview_data).pack(side=tk.LEFT)
        ttk.Button(controls, text="Refresh", command=self.refresh_file_state).pack(side=tk.LEFT, padx=6)

        reader_container = ttk.Frame(parent)
        reader_container.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self.reader_canvas = tk.Canvas(reader_container, highlightthickness=0)
        self.reader_scrollbar = ttk.Scrollbar(reader_container, orient=tk.VERTICAL, command=self._reader_on_scrollbar)
        self.reader_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.reader_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.reader_canvas.configure(yscrollcommand=self._reader_on_canvas_scroll)

        self.reader_inner_frame = ttk.Frame(self.reader_canvas)
        self.reader_canvas_window = self.reader_canvas.create_window((0, 0), window=self.reader_inner_frame, anchor="nw")

        self.reader_inner_frame.bind(
            "<Configure>",
            lambda _event: self.reader_canvas.configure(scrollregion=self.reader_canvas.bbox("all")),
        )
        self.reader_canvas.bind("<Configure>", self._reader_on_canvas_configure)
        self._reader_bind_mousewheel(self.reader_canvas)

        self._reader_show_placeholder("Load preview data to display translated content.")

        style = ttk.Style()
        style.configure("ReaderChapter.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("ReaderParagraph.TLabel", font=("Segoe UI", 10))
        style.configure("ReaderOriginal.TLabel", font=("Segoe UI", 9, "italic"), foreground="#555555")
    # ------------------------------------------------------------------
    # Queue processing
    # ------------------------------------------------------------------
    def _process_log_queue(self) -> None:
        while not self.log_queue.empty():
            message = self.log_queue.get()
            self.append_log(message)
        self.root.after(200, self._process_log_queue)

    def _process_request_queue(self) -> None:
        while True:
            try:
                event = self.request_queue.get_nowait()
            except queue.Empty:
                break
            self._update_request_history(event)
        self.root.after(250, self._process_request_queue)

    def _handle_translator_request(self, event: Dict[str, object]) -> None:
        self.request_queue.put(event)

    def _reset_request_history(self) -> None:
        self.request_items.clear()
        if hasattr(self, "request_tree"):
            for item in self.request_tree.get_children():
                self.request_tree.delete(item)
        while True:
            try:
                self.request_queue.get_nowait()
            except queue.Empty:
                break

    def _update_request_history(self, event: Dict[str, object]) -> None:
        request_id = event.get("id")
        if request_id is None:
            return
        status = str(event.get("status", "pending"))
        batch_size = event.get("batch_size")
        attempt = event.get("attempt")
        elapsed = event.get("elapsed")
        details = event.get("details") or event.get("detail") or ""
        item_id = f"request_{request_id}"
        label = f"Request #{request_id}"
        detail_parts = []
        if details:
            detail_parts.append(str(details))
        else:
            if batch_size:
                detail_parts.append(f"{int(batch_size)} items")
            if attempt:
                detail_parts.append(f"Attempt {attempt}")
        if isinstance(elapsed, (int, float)):
            detail_parts.append(f"{elapsed:.1f}s")
        detail_text = ", ".join(part for part in detail_parts if part)
        display_status = self._format_status_label(status)
        if not getattr(self, "request_tree", None):
            return
        exists = self.request_tree.exists(item_id)
        if status != "pending" and not detail_text and exists:
            current_values = self.request_tree.item(item_id, "values") or ("", "")
            detail_text = current_values[1] if len(current_values) > 1 else ""
        values = (display_status, detail_text)
        if not exists:
            self.request_tree.insert("", tk.END, iid=item_id, text=label, values=values, tags=(status,))
        else:
            self.request_tree.item(item_id, text=label, values=values, tags=(status,))
        self.request_tree.see(item_id)
        try:
            key = int(request_id)
        except (ValueError, TypeError):
            key = request_id
        self.request_items[key] = {"status": status, "details": detail_text}

    @staticmethod
    def _format_status_label(status: str) -> str:
        return status if status.isdigit() else status.capitalize()
    # ------------------------------------------------------------------
    # File state helpers
    # ------------------------------------------------------------------
    def refresh_file_state(self) -> None:
        self._ensure_output_subdirs(self.output_dir)

        xml_candidates = [p for p in self.output_dir.glob("*.xml") if not p.name.endswith("_translated.xml")]
        self.xml_path = xml_candidates[0] if xml_candidates else None

        if self.xml_path:
            self.output_dir = self.xml_path.parent
            self._ensure_output_subdirs(self.output_dir)

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
            except Exception as exc:
                self.append_log(f"Failed to read progress file: {exc}\n")

        if self.xml_path and self.xml_path.exists():
            try:
                with open(self.xml_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                info["total_count"] = len(data.get("paragraphs", []))
            except Exception:
                pass

        if info["total_count"]:
            info["completion_percentage"] = (
                info["translated_count"] / info["total_count"] * 100
            )

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
    def _choose_epub(self) -> None:
        file_path = filedialog.askopenfilename(filetypes=[("EPUB files", "*.epub")])
        if file_path:
            selected_path = Path(file_path)
            self.epub_path_var.set(file_path)
            self._update_output_dir_for_input(selected_path)
            self.refresh_file_state()

    def _apply_prompt_template(self, template: str) -> None:
        self.prompt_text.delete("1.0", tk.END)
        self.prompt_text.insert("1.0", template)

    def _save_prompt_to_file(self) -> None:
        prompt = self.prompt_text.get("1.0", tk.END).strip()
        if not prompt:
            messagebox.showinfo("Save prompt", "Prompt is empty; nothing to save.")
            return

        file_path = filedialog.asksaveasfilename(
            title="Save prompt",
            defaultextension=".txt",
            filetypes=[
                ("Text files", "*.txt"),
                ("Markdown files", "*.md"),
                ("All files", "*.*"),
            ],
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", encoding="utf-8") as handle:
                handle.write(prompt)
        except OSError as exc:
            messagebox.showerror("Save prompt", f"Could not save prompt:\n{exc}")
            return

        self.append_log(f"Prompt saved to {file_path}\n")
        messagebox.showinfo("Save prompt", f"Prompt saved to {Path(file_path).name}.")

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
        if message.endswith("\n"):
            print(message, end="")
        else:
            print(message)
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
        selected_path = Path(epub_path)
        self._update_output_dir_for_input(selected_path)

        if not selected_path.exists():
            messagebox.showerror("Conversion", "The selected EPUB file does not exist.")
            return

        self.status_var.set("Converting EPUB...")
        self.append_log("Starting EPUB to XML conversion...\n")

        def worker() -> None:
            success = False
            error: Optional[str] = None
            try:
                processor = EbookProcessor(str(selected_path), str(self.output_dir))
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
            self.status_var.set("Conversion complete.")
            self.append_log("Conversion completed successfully.\n")
        else:
            self.status_var.set("Conversion failed.")
            if error:
                messagebox.showerror("Conversion", f"Conversion failed: {error}")
            else:
                messagebox.showerror("Conversion", "Conversion failed. Please check logs for details.")
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

        translator.set_request_callback(self._handle_translator_request)
        self._reset_request_history()

        self.translation_running = True
        self.active_translator = translator
        translate_xml.stop_flag = False
        self.status_var.set("Translating.")
        self.append_log("Starting translation.\n")
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
        metadata = {
            "title": self.metadata_title_var.get().strip(),
            "author": self.metadata_author_var.get().strip(),
            "language": self.metadata_language_var.get().strip() or "vi",
            "identifier": self.metadata_identifier_var.get().strip(),
            "publisher": self.metadata_publisher_var.get().strip(),
        }
        metadata = {key: value for key, value in metadata.items() if value}

        try:
            create_epub_from_xml(
                str(self.translated_xml_path),
                target_name,
                str(self.output_dir),
                metadata=metadata,
            )
            self.append_log(f"Created EPUB: {target_name}\n")
            messagebox.showinfo(
                "Create EPUB",
                f"EPUB file '{target_name}' created in {self.output_dir.resolve()}"
            )
        except Exception as exc:
            messagebox.showerror("Create EPUB", f"Failed to create EPUB: {exc}")

    # ------------------------------------------------------------------
    # Preview loading and reader helpers
    # ------------------------------------------------------------------
    def load_preview_data(self) -> None:
        if self.reader_loading:
            messagebox.showinfo("Preview", "Preview data is already loading. Please wait.")
            return

        if not self.xml_path or not self.xml_path.exists():
            messagebox.showwarning("Preview", "No XML file found. Please run the conversion first.")
            return

        self.reader_loading = True
        load_token: object = object()
        self.reader_load_token = load_token
        self._reader_show_placeholder("Loading preview data...")

        worker = threading.Thread(
            target=self._preview_loader_worker,
            args=(load_token,),
            daemon=True,
        )
        worker.start()

    def _preview_loader_worker(self, token: object) -> None:
        try:
            paragraphs, chapter_ranges, messages = self._collect_preview_dataset()
            error: Optional[str] = None
        except Exception as exc:
            paragraphs = []
            chapter_ranges = []
            messages = []
            error = str(exc)

        def finish() -> None:
            if self.reader_load_token is not token:
                return
            self.reader_loading = False
            self.reader_load_token = None
            for message in messages:
                self.append_log(f"{message}\n")
            if error:
                self._reader_show_placeholder("Failed to load preview data.")
                messagebox.showerror("Preview", f"Failed to load preview data: {error}")
                return
            self.reader_paragraphs = paragraphs
            self.reader_chapter_ranges = chapter_ranges
            if not chapter_ranges:
                self._reader_show_placeholder("No previewable content found in the XML.")
            else:
                self._reset_reader_view()

        self.root.after(0, finish)

    def _collect_preview_dataset(self) -> tuple[list[Dict[str, object]], list[tuple[int, int]], list[str]]:
        messages: list[str] = []
        import xml.etree.ElementTree as ET

        original_tree = ET.parse(self.xml_path)
        original_root = original_tree.getroot()

        translation_map: Dict[str, str] = {}
        if self.progress_file and self.progress_file.exists():
            try:
                with open(self.progress_file, "r", encoding="utf-8") as progress_handle:
                    translation_map.update(json.load(progress_handle))
            except Exception as exc:
                messages.append(f"Could not read progress file for preview: {exc}")

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
                messages.append(f"Could not read translated XML for preview: {exc}")

        paragraphs: list[Dict[str, object]] = []
        chapter_ranges: list[tuple[int, int]] = []
        chapter_counter = 0
        for chapter in original_root.findall("chapter"):
            chapter_counter += 1
            chapter_id = chapter.get("id", f"chapter_{chapter_counter}")
            chapter_title = chapter.get("title", f"Chapter {chapter_counter}")
            translated_title = translation_map.get(f"{chapter_id}_title", chapter_title)

            start_index = len(paragraphs)
            paragraphs.append(
                {
                    "type": "chapter",
                    "text": translated_title or chapter_title,
                    "chapter_id": chapter_id,
                }
            )

            for item in chapter:
                if item.tag == "paragraph":
                    para_id = item.get("id")
                    text_elem = item.find("text")
                    original_text = text_elem.text.strip() if text_elem is not None and text_elem.text else ""
                    translated_text = translation_map.get(para_id, original_text) if para_id else original_text
                    if not (original_text or translated_text):
                        continue
                    paragraphs.append(
                        {
                            "type": "paragraph",
                            "translation": translated_text,
                            "original": original_text,
                            "role": item.get("role", ""),
                        }
                    )
                elif item.tag == "image":
                    img_src = item.get("src", "")
                    img_alt = item.get("alt", "Image")
                    absolute_path = (self.xml_path.parent / img_src).resolve() if img_src else None
                    paragraphs.append(
                        {
                            "type": "image",
                            "src": img_src,
                            "alt": img_alt,
                            "path": str(absolute_path) if absolute_path else "",
                        }
                    )

            end_index = len(paragraphs)
            if end_index > start_index:
                chapter_ranges.append((start_index, end_index))

        return paragraphs, chapter_ranges, messages

    def _reader_show_placeholder(self, message: str) -> None:
        self._reader_clear()
        self.reader_placeholder_label = ttk.Label(
            self.reader_inner_frame,
            text=message,
            anchor=tk.W,
            wraplength=self.reader_wraplength,
            justify=tk.LEFT,
        )
        self.reader_placeholder_label.pack(fill=tk.X, padx=8, pady=12)
    def _reset_reader_view(self) -> None:
        self._reader_clear()
        total_chunks = len(self.reader_chapter_ranges)
        if total_chunks == 0:
            self._reader_show_placeholder("No previewable content found in the XML.")
            return

        self.reader_total_chunks = total_chunks
        self._reader_load_chunk(0, position="end")
        self.reader_canvas.yview_moveto(0)
        self._reader_check_load()

    def _reader_clear(self) -> None:
        for child in self.reader_inner_frame.winfo_children():
            child.destroy()
        self.reader_chunk_frames.clear()
        self.reader_chunk_widgets.clear()
        self.reader_image_cache.clear()
        self.reader_loaded_chunks.clear()
        self.reader_total_chunks = 0
        self.reader_chapter_ranges = []
        self.reader_canvas.configure(scrollregion=(0, 0, 0, 0))
        self.reader_canvas.yview_moveto(0)
        self.reader_placeholder_label = None

    def _reader_load_chunk(self, chunk_index: int, position: str = "end") -> None:
        if chunk_index < 0 or chunk_index >= self.reader_total_chunks:
            return
        if chunk_index in self.reader_chunk_frames:
            return

        start, end = self.reader_chapter_ranges[chunk_index]
        frame = ttk.Frame(self.reader_inner_frame)
        widgets, images = self._populate_reader_chunk(frame, start, end)

        if position == "end":
            frame.pack(fill=tk.X, padx=4, pady=(0, 12))
            self.reader_loaded_chunks.append(chunk_index)
        else:
            children = [child for child in reversed(self.reader_inner_frame.pack_slaves())]
            if children:
                frame.pack(fill=tk.X, padx=4, pady=(0, 12), before=children[0])
            else:
                frame.pack(fill=tk.X, padx=4, pady=(0, 12))
            self.reader_loaded_chunks.insert(0, chunk_index)

        self.reader_chunk_frames[chunk_index] = frame
        self.reader_chunk_widgets[chunk_index] = widgets
        self.reader_image_cache[chunk_index] = images

    def _populate_reader_chunk(self, container: ttk.Frame, start: int, end: int) -> tuple[list[tk.Widget], list[ImageTk.PhotoImage]]:
        widgets: list[tk.Widget] = []
        images: list[ImageTk.PhotoImage] = []
        for item in self.reader_paragraphs[start:end]:
            item_type = item.get("type")
            if item_type == "chapter":
                lbl = ttk.Label(
                    container,
                    text=item.get("text", ""),
                    style="ReaderChapter.TLabel",
                    anchor=tk.W,
                    wraplength=self.reader_wraplength,
                    justify=tk.LEFT,
                )
                lbl.pack(fill=tk.X, padx=8, pady=(16, 6))
                widgets.append(lbl)
            elif item_type == "paragraph":
                translation = item.get("translation", "")
                original = item.get("original", "")
                display_text = translation or original
                if not display_text:
                    continue
                para_label = ttk.Label(
                    container,
                    text=display_text,
                    style="ReaderParagraph.TLabel",
                    wraplength=self.reader_wraplength,
                    justify=tk.LEFT,
                )
                para_label.pack(fill=tk.X, padx=16, pady=(4, 2))
                widgets.append(para_label)
                if translation and original and original.strip() and original.strip() != translation.strip():
                    original_label = ttk.Label(
                        container,
                        text=original,
                        style="ReaderOriginal.TLabel",
                        wraplength=self.reader_wraplength,
                        justify=tk.LEFT,
                    )
                    original_label.pack(fill=tk.X, padx=24, pady=(0, 6))
                    widgets.append(original_label)
            elif item_type == "image":
                alt_text = item.get("alt") or "Image"
                path_text = item.get("path") or item.get("src") or ""
                img_frame = ttk.Frame(container)
                img_frame.pack(fill=tk.X, padx=16, pady=(8, 10))
                image_loaded = False
                candidate: Optional[Path] = None
                if path_text:
                    candidate = Path(path_text)
                    if not candidate.is_absolute() and self.xml_path:
                        candidate = (self.xml_path.parent / candidate).resolve()
                if candidate and candidate.exists():
                    try:
                        with Image.open(candidate) as pil_image:
                            preview_image = pil_image.copy()
                        preview_image.thumbnail((self.reader_wraplength, 480), Image.LANCZOS)
                        photo = ImageTk.PhotoImage(preview_image)
                        images.append(photo)
                        img_label = ttk.Label(img_frame, image=photo)
                        img_label.image = photo
                        img_label.pack(anchor=tk.CENTER)
                        widgets.append(img_label)
                        image_loaded = True
                    except Exception as exc:
                        error_label = ttk.Label(
                            img_frame,
                            text=f"[Image load error] {alt_text}: {exc}",
                            style="ReaderParagraph.TLabel",
                            wraplength=self.reader_wraplength,
                            justify=tk.LEFT,
                        )
                        error_label.pack(fill=tk.X)
                        widgets.append(error_label)
                if not image_loaded:
                    placeholder = ttk.Label(
                        img_frame,
                        text=f"[Image missing] {alt_text}",
                        style="ReaderParagraph.TLabel",
                        wraplength=self.reader_wraplength,
                        justify=tk.LEFT,
                    )
                    placeholder.pack(fill=tk.X)
                    widgets.append(placeholder)
                if alt_text:
                    caption = ttk.Label(
                        img_frame,
                        text=alt_text,
                        style="ReaderOriginal.TLabel",
                        wraplength=self.reader_wraplength,
                        justify=tk.LEFT,
                    )
                    caption.pack(fill=tk.X, pady=(4, 0))
                    widgets.append(caption)
        return widgets, images
    def _reader_on_canvas_configure(self, event: tk.Event) -> None:
        self.reader_canvas.itemconfigure(self.reader_canvas_window, width=event.width)
        new_wrap = max(event.width - 40, 400)
        if new_wrap != self.reader_wraplength:
            self.reader_wraplength = new_wrap
            for widgets in self.reader_chunk_widgets.values():
                for widget in widgets:
                    try:
                        widget.configure(wraplength=self.reader_wraplength)
                    except tk.TclError:
                        continue

    def _reader_on_canvas_scroll(self, first: str, last: str) -> None:
        self.reader_scrollbar.set(first, last)
        self._reader_check_load()

    def _reader_on_scrollbar(self, *args: str) -> None:
        self.reader_canvas.yview(*args)
        self._reader_check_load()

    def _reader_on_mousewheel(self, event: tk.Event) -> str:
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            self.reader_canvas.yview_scroll(-1, "units")
        else:
            self.reader_canvas.yview_scroll(1, "units")
        self._reader_check_load()
        return "break"

    def _reader_bind_mousewheel(self, widget: tk.Widget) -> None:
        widget.bind("<Enter>", lambda _event: widget.focus_set())
        widget.bind("<MouseWheel>", self._reader_on_mousewheel)
        widget.bind("<Button-4>", self._reader_on_mousewheel)
        widget.bind("<Button-5>", self._reader_on_mousewheel)

    def _reader_check_load(self) -> None:
        if not self.reader_loaded_chunks:
            return
        first, last = self.reader_canvas.yview()
        if last > 0.9:
            next_chunk = self.reader_loaded_chunks[-1] + 1
            if next_chunk < self.reader_total_chunks:
                self._reader_load_chunk(next_chunk, position="end")
                self._reader_prune_chunks(direction="start")
        if first < 0.1:
            previous_chunk = self.reader_loaded_chunks[0] - 1
            if previous_chunk >= 0:
                self._reader_load_chunk(previous_chunk, position="start")
                self._reader_prune_chunks(direction="end")

    def _reader_prune_chunks(self, direction: str) -> None:
        while len(self.reader_loaded_chunks) > self.reader_max_loaded_chunks:
            if direction == "start":
                remove_index = self.reader_loaded_chunks.pop(0)
            else:
                remove_index = self.reader_loaded_chunks.pop()
            frame = self.reader_chunk_frames.pop(remove_index, None)
            self.reader_chunk_widgets.pop(remove_index, None)
            self.reader_image_cache.pop(remove_index, None)
            if frame is None:
                continue
            frame_height = frame.winfo_height()
            frame.destroy()
            if direction == "start":
                bbox = self.reader_canvas.bbox("all")
                if bbox:
                    x0, y0, x1, y1 = bbox
                    total_height = y1 - y0
                    if total_height > 0:
                        current_y = max(self.reader_canvas.canvasy(0) - frame_height, 0)
                        self.reader_canvas.yview_moveto(current_y / total_height)
    def _ensure_output_subdirs(self, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        (target / "images").mkdir(exist_ok=True)

    def _update_output_dir_for_input(self, source: Path) -> None:
        safe_stem = source.stem or "epub"
        self.output_dir = self.base_output_dir / f"output_{safe_stem}"
        self._ensure_output_subdirs(self.output_dir)
        self.final_epub_name_var.set(f"{safe_stem}_translated.epub")
        display_title = safe_stem.replace('_', ' ').strip() or safe_stem
        if not self.metadata_title_var.get().strip():
            self.metadata_title_var.set(display_title)
        if not self.metadata_identifier_var.get().strip():
            self.metadata_identifier_var.set(safe_stem)


def main() -> None:
    root = tk.Tk()
    app = EpubTranslatorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
