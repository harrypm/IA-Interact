#!/usr/bin/env python3
import os
import threading
from urllib.parse import quote, urlparse
try:
    import requests
except ModuleNotFoundError as e:
    raise SystemExit(
        "Missing dependency 'requests'. Install it with: python3 -m pip install requests"
    ) from e
try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText
except ModuleNotFoundError as e:
    raise SystemExit(
        "Tkinter is not available in this Python environment. "
        "Install tkinter support (for example: sudo apt install python3-tk) "
        "or run this script with a Python build that includes Tk support."
    ) from e


class IAInteractGUI(tk.Tk):
    COLOR_BG = "#0f1218"
    COLOR_PANEL = "#161b24"
    COLOR_PANEL_ALT = "#1c2330"
    COLOR_ENTRY = "#0c1016"
    COLOR_BORDER = "#2a3445"
    COLOR_TEXT = "#e6ebf5"
    COLOR_MUTED = "#aab6c9"
    COLOR_ACCENT = "#4d8edb"
    COLOR_ACCENT_ACTIVE = "#63a0e8"
    COLOR_ACCENT_MUTED = "#2d3f5b"
    COLOR_SELECTION_BG = "#2f6eb6"
    COLOR_SELECTION_FG = "#ffffff"
    def __init__(self):
        super().__init__()
        self._configure_scaling()
        self.title("IA Interact GUI")
        self.geometry("1040x740")
        self.minsize(940, 640)
        self._configure_dark_theme()

        self.access_key = ""
        self.secret_key = ""
        self.current_identifier = ""

        self.remote_files = []
        self.local_files = []

        self.access_key_var = tk.StringVar()
        self.secret_key_var = tk.StringVar()
        self.repo_var = tk.StringVar()
        self.target_directory_var = tk.StringVar(value="uploads")

        self.login_frame = None
        self.main_frame = None
        self.remote_listbox = None
        self.local_listbox = None
        self.status_text = None

        self._build_login_screen()

    def _configure_scaling(self):
        try:
            dpi = self.winfo_fpixels("1i")
            scale = max(1.0, min(1.6, dpi / 96.0))
            self.tk.call("tk", "scaling", scale)
        except tk.TclError:
            pass

    def _configure_dark_theme(self):
        self.configure(bg=self.COLOR_BG)
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure(
            ".",
            background=self.COLOR_BG,
            foreground=self.COLOR_TEXT,
            fieldbackground=self.COLOR_ENTRY,
            troughcolor=self.COLOR_PANEL,
            bordercolor=self.COLOR_BORDER,
            lightcolor=self.COLOR_BORDER,
            darkcolor=self.COLOR_BORDER,
            insertcolor=self.COLOR_TEXT,
        )
        style.configure("TFrame", background=self.COLOR_BG)
        style.configure("Card.TFrame", background=self.COLOR_PANEL)
        style.configure("TLabelframe", background=self.COLOR_BG, bordercolor=self.COLOR_BORDER)
        style.configure("TLabelframe.Label", background=self.COLOR_BG, foreground=self.COLOR_MUTED)
        style.configure("Card.TLabelframe", background=self.COLOR_PANEL, bordercolor=self.COLOR_BORDER)
        style.configure("Card.TLabelframe.Label", background=self.COLOR_PANEL, foreground=self.COLOR_TEXT)
        style.configure("TLabel", background=self.COLOR_BG, foreground=self.COLOR_TEXT)
        style.configure("Card.TLabel", background=self.COLOR_PANEL, foreground=self.COLOR_TEXT)
        style.configure(
            "Header.TLabel",
            background=self.COLOR_PANEL,
            foreground=self.COLOR_TEXT,
            font=("TkDefaultFont", 16, "bold"),
        )
        style.configure("TEntry", fieldbackground=self.COLOR_ENTRY, foreground=self.COLOR_TEXT, insertcolor=self.COLOR_TEXT)
        style.map("TEntry", fieldbackground=[("disabled", self.COLOR_PANEL_ALT)])
        style.configure("TButton", background=self.COLOR_PANEL_ALT, foreground=self.COLOR_TEXT, padding=(10, 6))
        style.map(
            "TButton",
            background=[
                ("active", self.COLOR_BORDER),
                ("pressed", self.COLOR_BORDER),
                ("disabled", self.COLOR_PANEL_ALT),
            ],
            foreground=[("disabled", self.COLOR_MUTED)],
        )
        style.configure("Accent.TButton", background=self.COLOR_ACCENT, foreground=self.COLOR_SELECTION_FG, padding=(10, 6))
        style.map(
            "Accent.TButton",
            background=[
                ("active", self.COLOR_ACCENT_ACTIVE),
                ("pressed", self.COLOR_ACCENT_ACTIVE),
                ("disabled", self.COLOR_ACCENT_MUTED),
            ],
            foreground=[("disabled", self.COLOR_MUTED)],
        )
        style.configure(
            "Vertical.TScrollbar",
            background=self.COLOR_PANEL_ALT,
            troughcolor=self.COLOR_PANEL,
            bordercolor=self.COLOR_BORDER,
            arrowcolor=self.COLOR_TEXT,
            darkcolor=self.COLOR_PANEL_ALT,
            lightcolor=self.COLOR_PANEL_ALT,
        )
        style.configure(
            "Horizontal.TScrollbar",
            background=self.COLOR_PANEL_ALT,
            troughcolor=self.COLOR_PANEL,
            bordercolor=self.COLOR_BORDER,
            arrowcolor=self.COLOR_TEXT,
            darkcolor=self.COLOR_PANEL_ALT,
            lightcolor=self.COLOR_PANEL_ALT,
        )

    def _apply_dark_text_widget_theme(self, widget):
        widget.configure(
            bg=self.COLOR_ENTRY,
            fg=self.COLOR_TEXT,
            selectbackground=self.COLOR_SELECTION_BG,
            selectforeground=self.COLOR_SELECTION_FG,
            highlightthickness=1,
            highlightbackground=self.COLOR_BORDER,
            highlightcolor=self.COLOR_ACCENT,
            borderwidth=0,
            relief="flat",
        )

    @staticmethod
    def _extract_identifier_from_archive_url(value):
        candidate = value
        if "://" not in candidate and candidate.startswith("archive.org/"):
            candidate = f"https://{candidate}"

        try:
            parsed = urlparse(candidate)
        except ValueError:
            return None

        host = (parsed.hostname or "").lower()
        if host not in ("archive.org", "www.archive.org"):
            return None

        path_parts = [part for part in parsed.path.split("/") if part]
        if not path_parts:
            return None

        if path_parts[0] in ("details", "download", "metadata"):
            if len(path_parts) < 2:
                return None
            return path_parts[1]

        return path_parts[0]

    @staticmethod
    def extract_repo_identifier(repo_input):
        value = repo_input.strip().strip('"').strip("'")
        if not value:
            return None

        identifier = IAInteractGUI._extract_identifier_from_archive_url(value)
        if identifier:
            return identifier

        if "/" not in value and " " not in value:
            return value

        return None

    def _build_login_screen(self):
        self.login_frame = ttk.Frame(self, padding=20, style="TFrame")
        self.login_frame.pack(fill="both", expand=True)

        card = ttk.Frame(self.login_frame, padding=20, style="Card.TFrame")
        card.pack(expand=True)

        ttk.Label(card, text="IA Interact Login", style="Header.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 16)
        )

        ttk.Label(card, text="S3 Access Key", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 8))
        ttk.Entry(card, textvariable=self.access_key_var, width=56).grid(row=1, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(card, text="S3 Secret Key", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=(0, 16))
        ttk.Entry(card, textvariable=self.secret_key_var, show="*", width=56).grid(row=2, column=1, sticky="ew", pady=(0, 16))

        ttk.Button(card, text="Login", command=self._handle_login, style="Accent.TButton").grid(row=3, column=0, columnspan=2, sticky="ew")
        card.columnconfigure(1, weight=1)

    def _handle_login(self):
        access_key = self.access_key_var.get().strip()
        secret_key = self.secret_key_var.get().strip()

        if not access_key or not secret_key:
            messagebox.showerror("Missing credentials", "Both S3 Access Key and S3 Secret Key are required.")
            return

        self.access_key = access_key
        self.secret_key = secret_key

        os.environ["S3_ACCESS_KEY"] = access_key
        os.environ["S3_SECRET_KEY"] = secret_key

        self.login_frame.destroy()
        self._build_main_screen()
        self.append_status("Login complete. Credentials loaded for this session.")

    def _build_main_screen(self):
        self.main_frame = ttk.Frame(self, padding=12, style="TFrame")
        self.main_frame.pack(fill="both", expand=True)

        repo_frame = ttk.LabelFrame(self.main_frame, text="Repository", padding=10, style="Card.TLabelframe")
        repo_frame.pack(fill="x")
        ttk.Label(repo_frame, text="Repository Link or Identifier", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(repo_frame, textvariable=self.repo_var, width=80).grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(4, 0))
        ttk.Button(repo_frame, text="Load Repository Files", command=self.load_repository_files, style="Accent.TButton").grid(row=1, column=1, sticky="ew", pady=(4, 0))
        repo_frame.columnconfigure(0, weight=1)

        list_container = ttk.Frame(self.main_frame, style="TFrame")
        list_container.pack(fill="both", expand=True, pady=(12, 8))
        list_container.columnconfigure(0, weight=1)
        list_container.columnconfigure(1, weight=1)
        list_container.rowconfigure(0, weight=1)

        remote_frame = ttk.LabelFrame(list_container, text="Repository Files (select for download)", padding=8, style="Card.TLabelframe")
        remote_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        remote_frame.columnconfigure(0, weight=1)
        remote_frame.rowconfigure(0, weight=1)

        self.remote_listbox = tk.Listbox(
            remote_frame,
            selectmode=tk.EXTENDED,
            exportselection=False,
            activestyle="none",
            height=16,
        )
        self._apply_dark_text_widget_theme(self.remote_listbox)
        remote_scroll = ttk.Scrollbar(remote_frame, orient="vertical", command=self.remote_listbox.yview)
        remote_scroll_x = ttk.Scrollbar(remote_frame, orient="horizontal", command=self.remote_listbox.xview)
        self.remote_listbox.configure(yscrollcommand=remote_scroll.set, xscrollcommand=remote_scroll_x.set)
        self.remote_listbox.grid(row=0, column=0, sticky="nsew")
        remote_scroll.grid(row=0, column=1, sticky="ns")
        remote_scroll_x.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        local_frame = ttk.LabelFrame(list_container, text="Local Files (select for upload)", padding=8, style="Card.TLabelframe")
        local_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        local_frame.columnconfigure(0, weight=1)
        local_frame.rowconfigure(0, weight=1)

        self.local_listbox = tk.Listbox(
            local_frame,
            selectmode=tk.EXTENDED,
            exportselection=False,
            activestyle="none",
            height=16,
        )
        self._apply_dark_text_widget_theme(self.local_listbox)
        local_scroll = ttk.Scrollbar(local_frame, orient="vertical", command=self.local_listbox.yview)
        local_scroll_x = ttk.Scrollbar(local_frame, orient="horizontal", command=self.local_listbox.xview)
        self.local_listbox.configure(yscrollcommand=local_scroll.set, xscrollcommand=local_scroll_x.set)
        self.local_listbox.grid(row=0, column=0, sticky="nsew")
        local_scroll.grid(row=0, column=1, sticky="ns")
        local_scroll_x.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        local_buttons = ttk.Frame(local_frame, style="Card.TFrame")
        local_buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(local_buttons, text="Add Files", command=self.select_local_files).pack(side="left")
        ttk.Button(local_buttons, text="Remove Selected", command=self.remove_selected_local_files).pack(side="left", padx=8)
        ttk.Button(local_buttons, text="Clear", command=self.clear_local_files).pack(side="left")

        action_frame = ttk.LabelFrame(self.main_frame, text="Actions", padding=10, style="Card.TLabelframe")
        action_frame.pack(fill="x")
        action_frame.columnconfigure(1, weight=1)
        ttk.Label(action_frame, text="Target upload directory", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(action_frame, textvariable=self.target_directory_var).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(action_frame, text="Upload Selected Local Files", command=self.upload_selected_local_files, style="Accent.TButton").grid(row=0, column=2, sticky="ew")
        ttk.Button(action_frame, text="Download Selected Repository Files", command=self.download_selected_repository_files, style="Accent.TButton").grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0)
        )

        status_frame = ttk.LabelFrame(self.main_frame, text="Status", padding=8, style="Card.TLabelframe")
        status_frame.pack(fill="both", expand=True, pady=(8, 0))
        status_frame.columnconfigure(0, weight=1)
        status_frame.rowconfigure(0, weight=1)

        self.status_text = ScrolledText(status_frame, height=10, wrap="word", state="disabled", padx=8, pady=8)
        self._apply_dark_text_widget_theme(self.status_text)
        self.status_text.grid(row=0, column=0, sticky="nsew")

    def append_status(self, message):
        def _append():
            self.status_text.configure(state="normal")
            self.status_text.insert("end", f"{message}\n")
            self.status_text.see("end")
            self.status_text.configure(state="disabled")

        if self.status_text is None:
            return
        self.status_text.after(0, _append)

    def _set_remote_files(self, files):
        self.remote_files = files
        self.remote_listbox.delete(0, "end")
        for file_name in files:
            self.remote_listbox.insert("end", file_name)

    def _refresh_local_files(self):
        self.local_listbox.delete(0, "end")
        for file_path in self.local_files:
            self.local_listbox.insert("end", file_path)

    def select_local_files(self):
        file_paths = filedialog.askopenfilenames(title="Select files to upload")
        if not file_paths:
            return

        for file_path in file_paths:
            if file_path not in self.local_files:
                self.local_files.append(file_path)

        self._refresh_local_files()
        self.append_status(f"Added {len(file_paths)} file(s) to upload list.")

    def remove_selected_local_files(self):
        selected_indices = list(self.local_listbox.curselection())
        if not selected_indices:
            return

        for index in reversed(selected_indices):
            del self.local_files[index]

        self._refresh_local_files()
        self.append_status("Removed selected local file(s) from upload list.")

    def clear_local_files(self):
        self.local_files.clear()
        self._refresh_local_files()
        self.append_status("Cleared local upload file list.")

    def load_repository_files(self):
        repo_value = self.repo_var.get().strip()
        identifier = self.extract_repo_identifier(repo_value)
        if not identifier:
            messagebox.showerror("Invalid repository", "Enter a valid archive.org/details/<identifier> link or identifier.")
            return
        self.append_status(f"Loading repository files for '{identifier}'...")

        def worker():
            files, error = self.fetch_repository_files(identifier)
            if error:
                self.remote_listbox.after(0, lambda: self._set_remote_files([]))
                self.append_status(error)
                return
            def set_files():
                self.current_identifier = identifier
                self._set_remote_files(files)
            self.remote_listbox.after(0, set_files)
            self.append_status(f"Loaded {len(files)} file(s) from '{identifier}'.")

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def fetch_repository_files(identifier):
        url = f"https://archive.org/metadata/{identifier}"
        try:
            response = requests.get(url, timeout=(30, 120))
        except requests.RequestException as e:
            return [], f"Repository request failed: {e}"

        if response.status_code != 200:
            return [], f"Repository metadata error: {response.status_code} {response.reason}"

        try:
            data = response.json()
        except ValueError:
            return [], "Repository metadata response was not valid JSON."

        files = []
        for file_info in data.get("files", []):
            name = file_info.get("name")
            if not name:
                continue
            parts = name.split("/")
            if any(part.endswith(".thumbs") for part in parts):
                continue
            files.append(name)

        return files, None

    def upload_selected_local_files(self):
        repo_identifier = self.extract_repo_identifier(self.repo_var.get().strip())
        identifier = repo_identifier or self.current_identifier
        if not identifier:
            messagebox.showerror("Repository missing", "Enter a valid repository link or identifier first.")
            return

        if identifier != self.current_identifier:
            self.current_identifier = identifier
            self.append_status(f"Using repository '{identifier}' for upload.")
        if not self.local_files:
            messagebox.showerror("No files selected", "Add one or more local files to upload.")
            return

        target_directory = self.target_directory_var.get().strip().strip("/")
        files_to_upload = list(self.local_files)

        self.append_status(
            f"Starting upload of {len(files_to_upload)} file(s) to '{identifier}' "
            f"directory '{target_directory or '(root)'}'."
        )

        def worker():
            success_count = 0
            for file_path in files_to_upload:
                ok, detail = self.upload_single_file(identifier, file_path, target_directory)
                self.append_status(detail)
                if ok:
                    success_count += 1
            self.append_status(f"Upload complete: {success_count}/{len(files_to_upload)} succeeded.")

        threading.Thread(target=worker, daemon=True).start()

    def upload_single_file(self, identifier, file_path, directory):
        if not os.path.isfile(file_path):
            return False, f"Skipped missing file: {file_path}"

        object_name = os.path.basename(file_path)
        object_path = f"{directory}/{object_name}" if directory else object_name
        upload_url = f"https://s3.us.archive.org/{identifier}/{quote(object_path, safe='/')}"
        headers = {
            "x-amz-auto-make-bucket": "1",
            "Authorization": f"AWS {self.access_key}:{self.secret_key}",
        }

        try:
            with open(file_path, "rb") as file_data:
                response = requests.put(upload_url, headers=headers, data=file_data, timeout=(60, 600))
        except requests.RequestException as e:
            return False, f"Upload failed for '{file_path}': {e}"
        except OSError as e:
            return False, f"Upload failed opening '{file_path}': {e}"

        if response.status_code != 200:
            return False, f"Upload failed for '{file_path}': {response.status_code} {response.reason}"

        return True, f"Uploaded: {file_path}"

    def download_selected_repository_files(self):
        repo_identifier = self.extract_repo_identifier(self.repo_var.get().strip())
        if repo_identifier and self.current_identifier and repo_identifier != self.current_identifier:
            messagebox.showerror(
                "Repository changed",
                "Repository field changed since files were loaded. Click 'Load Repository Files' to refresh list before downloading.",
            )
            return

        identifier = self.current_identifier or repo_identifier
        if not identifier:
            messagebox.showerror("Repository not loaded", "Load repository files first.")
            return
        if not self.remote_files:
            messagebox.showerror("No repository files loaded", "Load repository files first.")
            return

        selected_indices = list(self.remote_listbox.curselection())
        if not selected_indices:
            messagebox.showerror("No files selected", "Select one or more repository files to download.")
            return

        destination_dir = filedialog.askdirectory(title="Select destination folder")
        if not destination_dir:
            return

        selected_files = [self.remote_files[index] for index in selected_indices]
        self.append_status(f"Starting download of {len(selected_files)} file(s) to '{destination_dir}'.")

        def worker():
            success_count = 0
            for file_name in selected_files:
                ok, detail = self.download_single_file(identifier, file_name, destination_dir)
                self.append_status(detail)
                if ok:
                    success_count += 1
            self.append_status(f"Download complete: {success_count}/{len(selected_files)} succeeded.")

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def download_single_file(identifier, file_name, destination_dir):
        safe_relative_path = os.path.normpath(file_name).lstrip("/\\")
        if safe_relative_path.startswith(".."):
            return False, f"Skipped unsafe file path: {file_name}"

        output_path = os.path.join(destination_dir, safe_relative_path)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        download_url = f"https://archive.org/download/{identifier}/{quote(file_name, safe='/')}"
        try:
            response = requests.get(download_url, stream=True, timeout=(60, 600))
        except requests.RequestException as e:
            return False, f"Download failed for '{file_name}': {e}"

        if response.status_code != 200:
            return False, f"Download failed for '{file_name}': {response.status_code} {response.reason}"

        try:
            with open(output_path, "wb") as output_file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    output_file.write(chunk)
        except OSError as e:
            return False, f"Download failed writing '{output_path}': {e}"

        return True, f"Downloaded: {output_path}"


def main():
    app = IAInteractGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
