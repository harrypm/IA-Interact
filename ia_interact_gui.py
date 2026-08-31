#!/usr/bin/env python3
import os
import threading
import time
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

try:
    from ia_accounts import AccountStore
except Exception:
    AccountStore = None


def _fmt_bytes(n):
    if n is None:
        return "?"
    if n < 1024:
        return f"{n} B"
    value = float(n)
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        value /= 1024.0
        if value < 1024:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} PiB"


class ProgressFileWrapper:
    """Wraps an open binary file so requests streams the upload while we count bytes."""

    def __init__(self, file_obj, on_read):
        self._file = file_obj
        self._on_read = on_read

    def read(self, amt=-1):
        data = self._file.read(amt)
        if data:
            self._on_read(len(data))
        return data

    def __getattr__(self, name):
        return getattr(self._file, name)


class _ProgressReporter:
    """Thread-side progress bridge: funnels updates to the Tk thread via after()."""

    def __init__(self, gui, total_files, kind):
        self.gui = gui
        self.total_files = total_files
        self.kind = kind
        self.file_index = 0
        self.file_name = ""
        self.total_bytes = 0
        self.transferred = 0
        self._last_ui = 0.0
        self._file_start = 0.0

    def start_file(self, index, name, total_bytes):
        self.file_index = index
        self.file_name = name
        self.total_bytes = total_bytes or 0
        self.transferred = 0
        self._file_start = time.time()
        self._sync(True)

    def add_bytes(self, n):
        self.transferred += n
        now = time.time()
        if now - self._last_ui >= 0.1:
            self._last_ui = now
            self._sync(False)

    def finish_file(self):
        self._sync(True)

    def idle(self, text="Idle"):
        def _do():
            self.gui.progress_bar.configure(value=0)
            self.gui.progress_label.configure(text=text)
        self.gui.after(0, _do)

    def _sync(self, force):
        total = self.total_bytes
        transferred = self.transferred
        pct = min(100.0, transferred / total * 100.0) if total > 0 else 0.0
        elapsed = time.time() - self._file_start
        speed = transferred / elapsed if elapsed > 0 else 0.0
        index = self.file_index
        total_files = self.total_files
        name = self.file_name
        kind = self.kind

        def _do():
            self.gui.progress_bar.configure(value=pct)
            self.gui.progress_label.configure(
                text=(
                    f"{kind.capitalize()} {index}/{total_files}: {name}  "
                    f"{_fmt_bytes(transferred)} / {_fmt_bytes(total)} ({pct:.1f}%)  "
                    f"{_fmt_bytes(speed)}/s"
                )
            )
        self.gui.after(0, _do)


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

        self.account_store = AccountStore() if AccountStore is not None else None
        if self.account_store is not None:
            try:
                self.account_store.load()
            except Exception:
                pass
        self.current_profile = None

        self.cancel_event = threading.Event()
        self.profile_combobox = None
        self.current_profile_label = None
        self.progress_bar = None
        self.progress_label = None
        self.cancel_transfer_button = None
        self.upload_button = None
        self.download_button = None

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

        profile_row = 1
        if self.account_store is not None:
            ttk.Label(card, text="Saved Profile", style="Card.TLabel").grid(
                row=profile_row, column=0, sticky="w", pady=(0, 8)
            )
            profile_combo_frame = ttk.Frame(card, style="Card.TFrame")
            profile_combo_frame.grid(row=profile_row, column=1, sticky="ew", pady=(0, 8))
            self.profile_combobox = ttk.Combobox(
                profile_combo_frame, values=[], state="readonly", width=46
            )
            self.profile_combobox.pack(side="left", fill="x", expand=True)
            self.profile_combobox.bind("<<ComboboxSelected>>", self._on_profile_selected)
            ttk.Button(
                profile_combo_frame, text="Manage", command=self._open_login_account_manager, width=10
            ).pack(side="left", padx=(8, 0))
            profile_row += 1

        ttk.Label(card, text="S3 Access Key", style="Card.TLabel").grid(row=profile_row, column=0, sticky="w", pady=(0, 8))
        ttk.Entry(card, textvariable=self.access_key_var, width=56).grid(row=profile_row, column=1, sticky="ew", pady=(0, 8))
        profile_row += 1

        ttk.Label(card, text="S3 Secret Key", style="Card.TLabel").grid(row=profile_row, column=0, sticky="w", pady=(0, 16))
        ttk.Entry(card, textvariable=self.secret_key_var, show="*", width=56).grid(row=profile_row, column=1, sticky="ew", pady=(0, 16))
        profile_row += 1

        ttk.Button(card, text="Login", command=self._handle_login, style="Accent.TButton").grid(row=profile_row, column=0, columnspan=2, sticky="ew")
        card.columnconfigure(1, weight=1)

        if self.account_store is not None:
            self._refresh_login_profiles()
            try:
                active = self.account_store.get_active()
            except Exception:
                active = None
            if active:
                self.profile_combobox.set(active)
                self._on_profile_selected()

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

        selected = ""
        if self.profile_combobox is not None:
            selected = (self.profile_combobox.get() or "").strip()
        if selected and self.account_store is not None:
            try:
                if self.account_store.get_profile(selected):
                    self.account_store.set_active(selected)
                    self.current_profile = selected
            except Exception:
                pass

        self.login_frame.destroy()
        self._build_main_screen()
        self.append_status("Login complete. Credentials loaded for this session.")
        if self.current_profile:
            self.append_status(f"Active account profile: {self.current_profile}")

    def _build_main_screen(self):
        self.main_frame = ttk.Frame(self, padding=12, style="TFrame")
        self.main_frame.pack(fill="both", expand=True)

        account_bar = ttk.Frame(self.main_frame, style="TFrame")
        account_bar.pack(fill="x", pady=(0, 8))
        self.current_profile_label = ttk.Label(
            account_bar,
            text=f"Account: {self.current_profile or 'Manual (no profile)'}",
            style="TLabel",
        )
        self.current_profile_label.pack(side="left")
        if self.account_store is not None:
            ttk.Button(
                account_bar, text="Accounts", command=self._open_main_account_manager
            ).pack(side="right")

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
        self.upload_button = ttk.Button(action_frame, text="Upload Selected Local Files", command=self.upload_selected_local_files, style="Accent.TButton")
        self.upload_button.grid(row=0, column=2, sticky="ew")
        self.download_button = ttk.Button(action_frame, text="Download Selected Repository Files", command=self.download_selected_repository_files, style="Accent.TButton")
        self.download_button.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        progress_frame = ttk.LabelFrame(self.main_frame, text="Progress", padding=8, style="Card.TLabelframe")
        progress_frame.pack(fill="x", pady=(8, 0))
        progress_frame.columnconfigure(0, weight=1)

        self.progress_bar = ttk.Progressbar(progress_frame, mode="determinate", maximum=100)
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        self.cancel_transfer_button = ttk.Button(
            progress_frame, text="Cancel", command=self._cancel_transfer, state="disabled", width=10
        )
        self.cancel_transfer_button.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.progress_label = ttk.Label(progress_frame, text="Idle", style="Card.TLabel")
        self.progress_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

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

    def _set_transfer_running(self, running):
        def _do():
            if self.upload_button is not None:
                self.upload_button.configure(state="normal" if not running else "disabled")
            if self.download_button is not None:
                self.download_button.configure(state="normal" if not running else "disabled")
            if self.cancel_transfer_button is not None:
                self.cancel_transfer_button.configure(state="normal" if running else "disabled")
        self.after(0, _do)

    def _cancel_transfer(self):
        self.cancel_event.set()
        self.append_status("Cancel requested; finishing current operation...")

    def _refresh_login_profiles(self):
        if self.profile_combobox is None or self.account_store is None:
            return
        try:
            names = self.account_store.list_names()
        except Exception:
            names = []
        self.profile_combobox["values"] = names
        current = (self.profile_combobox.get() or "").strip()
        if current and current not in names:
            self.profile_combobox.set("")

    def _on_profile_selected(self, event=None):
        if self.profile_combobox is None or self.account_store is None:
            return
        name = (self.profile_combobox.get() or "").strip()
        if not name:
            return
        try:
            profile = self.account_store.get_profile(name)
        except Exception:
            profile = None
        if profile:
            self.access_key_var.set(profile.get("access_key", ""))
            self.secret_key_var.set(profile.get("secret_key", ""))

    def _refresh_profile_label(self):
        if self.current_profile_label is None:
            return
        name = self.current_profile or "Manual (no profile)"
        self.current_profile_label.configure(text=f"Account: {name}")

    def _refresh_account_ui(self):
        self._refresh_login_profiles()
        self._refresh_profile_label()

    def _open_login_account_manager(self):
        if self.account_store is None:
            return
        AccountManagerDialog(
            self,
            self.account_store,
            on_change=self._refresh_account_ui,
            mode="manage",
            switch_callback=None,
        )

    def _open_main_account_manager(self):
        if self.account_store is None:
            return
        AccountManagerDialog(
            self,
            self.account_store,
            on_change=self._refresh_account_ui,
            mode="switch",
            switch_callback=self._switch_account_from_dialog,
        )

    def _switch_account_from_dialog(self, name):
        return self._apply_account(name)

    def _apply_account(self, profile_name):
        if self.account_store is None:
            return False
        try:
            profile = self.account_store.get_profile(profile_name)
        except Exception:
            profile = None
        if not profile:
            messagebox.showerror("Account", f"Profile '{profile_name}' not found.")
            return False
        self.access_key = profile.get("access_key", "")
        self.secret_key = profile.get("secret_key", "")
        os.environ["S3_ACCESS_KEY"] = self.access_key
        os.environ["S3_SECRET_KEY"] = self.secret_key
        self.current_profile = profile_name
        try:
            self.account_store.set_active(profile_name)
        except Exception:
            pass
        self._refresh_profile_label()
        self.append_status(f"Switched to account '{profile_name}'.")
        return True

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
            reporter = _ProgressReporter(self, len(files_to_upload), "upload")
            success_count = 0
            for index, file_path in enumerate(files_to_upload, start=1):
                if self.cancel_event.is_set():
                    self.append_status("Upload cancelled by user.")
                    break
                ok, detail = self.upload_single_file(identifier, file_path, target_directory, reporter, index)
                self.append_status(detail)
                reporter.finish_file()
                if ok:
                    success_count += 1
            cancelled = self.cancel_event.is_set()
            self._set_transfer_running(False)
            if not cancelled:
                self.append_status(f"Upload complete: {success_count}/{len(files_to_upload)} succeeded.")
            reporter.idle("Cancelled" if cancelled else "Idle")

        self.cancel_event.clear()
        self._set_transfer_running(True)
        threading.Thread(target=worker, daemon=True).start()

    def upload_single_file(self, identifier, file_path, directory, reporter=None, file_index=1):
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
            total = os.path.getsize(file_path)
        except OSError:
            total = 0
        if reporter is not None:
            reporter.start_file(file_index, object_name, total)

        try:
            with open(file_path, "rb") as file_data:
                if reporter is not None:
                    body = ProgressFileWrapper(file_data, reporter.add_bytes)
                else:
                    body = file_data
                response = requests.put(upload_url, headers=headers, data=body, timeout=(60, 600))
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
            reporter = _ProgressReporter(self, len(selected_files), "download")
            success_count = 0
            for index, file_name in enumerate(selected_files, start=1):
                if self.cancel_event.is_set():
                    self.append_status("Download cancelled by user.")
                    break
                ok, detail = self.download_single_file(identifier, file_name, destination_dir, reporter, index)
                self.append_status(detail)
                reporter.finish_file()
                if ok:
                    success_count += 1
            cancelled = self.cancel_event.is_set()
            self._set_transfer_running(False)
            if not cancelled:
                self.append_status(f"Download complete: {success_count}/{len(selected_files)} succeeded.")
            reporter.idle("Cancelled" if cancelled else "Idle")

        self.cancel_event.clear()
        self._set_transfer_running(True)
        threading.Thread(target=worker, daemon=True).start()

    def download_single_file(self, identifier, file_name, destination_dir, reporter=None, file_index=1):
        safe_relative_path = os.path.normpath(file_name).lstrip("/\\")
        if safe_relative_path.startswith(".."):
            return False, f"Skipped unsafe file path: {file_name}"

        output_path = os.path.join(destination_dir, safe_relative_path)
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        download_url = f"https://archive.org/download/{identifier}/{quote(file_name, safe='/')}"
        try:
            response = requests.get(download_url, stream=True, timeout=(60, 600))
        except requests.RequestException as e:
            return False, f"Download failed for '{file_name}': {e}"

        if response.status_code != 200:
            return False, f"Download failed for '{file_name}': {response.status_code} {response.reason}"

        try:
            total_header = response.headers.get("content-length")
            total = int(total_header) if total_header else 0
        except (TypeError, ValueError):
            total = 0
        if reporter is not None:
            reporter.start_file(file_index, os.path.basename(file_name), total)

        cancelled = False
        try:
            with open(output_path, "wb") as output_file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    output_file.write(chunk)
                    if reporter is not None:
                        reporter.add_bytes(len(chunk))
                    if self.cancel_event.is_set():
                        cancelled = True
                        break
        except OSError as e:
            return False, f"Download failed writing '{output_path}': {e}"

        if cancelled:
            return False, f"Download cancelled: {file_name}"

        return True, f"Downloaded: {output_path}"


class AccountManagerDialog(tk.Toplevel):
    """Dialog to add/edit/remove saved S3 profiles, and (in switch mode) pick one to use."""

    def __init__(self, parent, store, on_change=None, mode="manage", switch_callback=None):
        super().__init__(parent)
        self.store = store
        self.on_change = on_change
        self.mode = mode
        self.switch_callback = switch_callback
        self.selected_name = None

        self.title("Account Manager")
        self.geometry("660x470")
        self.minsize(580, 400)
        self.configure(bg=IAInteractGUI.COLOR_BG)
        self.transient(parent)

        self.name_var = tk.StringVar()
        self.access_var = tk.StringVar()
        self.secret_var = tk.StringVar()
        self.notes_var = tk.StringVar()
        self.show_secret_var = tk.BooleanVar(value=False)

        self._build_ui()
        self._refresh_list()
        self.grab_set()
        self.focus_set()

    def _build_ui(self):
        root = ttk.Frame(self, padding=12, style="TFrame")
        root.pack(fill="both", expand=True)

        list_frame = ttk.LabelFrame(root, text="Saved Profiles", padding=8, style="Card.TLabelframe")
        list_frame.pack(fill="both", expand=True)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.profile_listbox = tk.Listbox(list_frame, height=8, activestyle="none")
        self._apply_listbox_theme(self.profile_listbox)
        list_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.profile_listbox.yview)
        self.profile_listbox.configure(yscrollcommand=list_scroll.set)
        self.profile_listbox.grid(row=0, column=0, sticky="nsew")
        list_scroll.grid(row=0, column=1, sticky="ns")
        self.profile_listbox.bind("<<ListboxSelect>>", self._on_list_select)
        self.profile_listbox.bind("<Double-Button-1>", self._on_list_double)

        form_frame = ttk.LabelFrame(root, text="Profile Details", padding=8, style="Card.TLabelframe")
        form_frame.pack(fill="x", pady=(8, 0))
        form_frame.columnconfigure(1, weight=1)

        ttk.Label(form_frame, text="Profile name", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Entry(form_frame, textvariable=self.name_var).grid(row=0, column=1, columnspan=2, sticky="ew", pady=(0, 6))

        ttk.Label(form_frame, text="S3 access key", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 6))
        ttk.Entry(form_frame, textvariable=self.access_var).grid(row=1, column=1, columnspan=2, sticky="ew", pady=(0, 6))

        ttk.Label(form_frame, text="S3 secret key", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=(0, 6))
        self._secret_entry = ttk.Entry(form_frame, textvariable=self.secret_var, show="*")
        self._secret_entry.grid(row=2, column=1, sticky="ew", pady=(0, 6))
        ttk.Checkbutton(form_frame, text="Show", variable=self.show_secret_var, command=self._toggle_secret).grid(
            row=2, column=2, padx=(8, 0), pady=(0, 6)
        )

        ttk.Label(form_frame, text="Notes", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=(0, 6))
        ttk.Entry(form_frame, textvariable=self.notes_var).grid(row=3, column=1, columnspan=2, sticky="ew", pady=(0, 6))

        button_frame = ttk.Frame(root, style="TFrame")
        button_frame.pack(fill="x", pady=(8, 0))
        ttk.Button(button_frame, text="Add", command=self._add).pack(side="left")
        ttk.Button(button_frame, text="Update", command=self._update).pack(side="left", padx=8)
        ttk.Button(button_frame, text="Remove", command=self._remove).pack(side="left")
        if self.mode == "switch" and self.switch_callback is not None:
            ttk.Button(button_frame, text="Use", command=self._use, style="Accent.TButton").pack(side="right")
        ttk.Button(button_frame, text="Close", command=self._close).pack(side="right")

    @staticmethod
    def _apply_listbox_theme(widget):
        widget.configure(
            bg=IAInteractGUI.COLOR_ENTRY,
            fg=IAInteractGUI.COLOR_TEXT,
            selectbackground=IAInteractGUI.COLOR_SELECTION_BG,
            selectforeground=IAInteractGUI.COLOR_SELECTION_FG,
            highlightthickness=1,
            highlightbackground=IAInteractGUI.COLOR_BORDER,
            highlightcolor=IAInteractGUI.COLOR_ACCENT,
            borderwidth=0,
            relief="flat",
        )

    def _toggle_secret(self):
        self._secret_entry.configure(show="" if self.show_secret_var.get() else "*")

    def _refresh_list(self):
        self.profile_listbox.delete(0, "end")
        try:
            names = self.store.list_names()
        except Exception:
            names = []
        for name in names:
            self.profile_listbox.insert("end", name)

    def _notify_change(self):
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                pass

    def _on_list_select(self, event=None):
        selection = self.profile_listbox.curselection()
        if not selection:
            return
        name = self.profile_listbox.get(selection[0])
        self.selected_name = name
        try:
            profile = self.store.get_profile(name)
        except Exception:
            profile = None
        if profile:
            self.name_var.set(profile.get("name", ""))
            self.access_var.set(profile.get("access_key", ""))
            self.secret_var.set(profile.get("secret_key", ""))
            self.notes_var.set(profile.get("notes", ""))

    def _on_list_double(self, event=None):
        if self.mode == "switch" and self.switch_callback is not None:
            self._use()

    def _clear_form(self):
        self.name_var.set("")
        self.access_var.set("")
        self.secret_var.set("")
        self.notes_var.set("")
        self.selected_name = None

    def _add(self):
        name = self.name_var.get().strip()
        access = self.access_var.get().strip()
        secret = self.secret_var.get().strip()
        notes = self.notes_var.get().strip()
        if not name or not access or not secret:
            messagebox.showerror("Missing fields", "Profile name, access key, and secret key are required.")
            return
        try:
            self.store.add_profile(name, access, secret, notes)
        except Exception as exc:
            messagebox.showerror("Add failed", str(exc))
            return
        self._refresh_list()
        self._notify_change()
        self._clear_form()
        self._log(f"Added profile '{name}'.")

    def _update(self):
        if not self.selected_name:
            messagebox.showerror("No selection", "Select a profile from the list to update.")
            return
        name = self.name_var.get().strip()
        access = self.access_var.get().strip()
        secret = self.secret_var.get().strip()
        notes = self.notes_var.get().strip()
        if not name or not access or not secret:
            messagebox.showerror("Missing fields", "Profile name, access key, and secret key are required.")
            return
        rename = name if name != self.selected_name else None
        try:
            self.store.update_profile(
                self.selected_name,
                access_key=access,
                secret_key=secret,
                notes=notes,
                rename=rename,
            )
        except Exception as exc:
            messagebox.showerror("Update failed", str(exc))
            return
        self.selected_name = name
        self._refresh_list()
        self._notify_change()
        self._log(f"Updated profile '{name}'.")

    def _remove(self):
        selection = self.profile_listbox.curselection()
        if not selection:
            messagebox.showerror("No selection", "Select a profile from the list to remove.")
            return
        name = self.profile_listbox.get(selection[0])
        if not messagebox.askyesno("Remove profile", f"Remove profile '{name}'? This cannot be undone."):
            return
        try:
            self.store.remove_profile(name)
        except Exception as exc:
            messagebox.showerror("Remove failed", str(exc))
            return
        if self.selected_name == name:
            self._clear_form()
        self._refresh_list()
        self._notify_change()
        self._log(f"Removed profile '{name}'.")

    def _use(self):
        selection = self.profile_listbox.curselection()
        name = None
        if selection:
            name = self.profile_listbox.get(selection[0])
        elif self.selected_name:
            name = self.selected_name
        if not name:
            messagebox.showerror("No selection", "Select a profile to use.")
            return
        if self.switch_callback is None:
            return
        try:
            ok = self.switch_callback(name)
        except Exception as exc:
            messagebox.showerror("Switch failed", str(exc))
            return
        if ok:
            self.destroy()

    def _close(self):
        self.destroy()

    def _log(self, message):
        parent = self.master
        if isinstance(parent, IAInteractGUI):
            parent.append_status(message)


def main():
    app = IAInteractGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
