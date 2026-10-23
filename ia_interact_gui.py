#!/usr/bin/env python3
import os
import re
import threading
import time
import webbrowser
from urllib.parse import quote, urlparse, unquote
from urllib.request import url2pathname
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

# Optional drag & drop support (files/folders dropped from the OS file manager).
# Importing tkinterdnd2 patches tkinter widgets with drop_target_register()/
# dnd_bind(); TkinterDnD.require(root) loads the tkdnd Tcl extension. The GUI
# works normally without it - drag & drop is simply disabled.
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
except Exception:
    TkinterDnD = None
    DND_FILES = None

try:
    from ia_accounts import AccountStore
except Exception:
    AccountStore = None

try:
    from version import __version__
except Exception:
    __version__ = "unknown"

try:
    import ia_update
except Exception:
    ia_update = None


_dpi_awareness_done = False


def _enable_windows_dpi_awareness():
    """Make this process DPI-aware on Windows (idempotent, no-op elsewhere).

    Must run before the Tk root is created. Without it Windows virtualizes the
    process to 96 DPI and DWM bitmap-stretches the window (blurry text), and
    the app cannot see the display's real scaling at all. Tk then derives its
    point-based font scaling from the real DPI automatically.
    """
    global _dpi_awareness_done
    if _dpi_awareness_done or os.name != "nt":
        return
    _dpi_awareness_done = True
    try:
        import ctypes
        try:
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (Windows 10 1703+)
            if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return
        except Exception:
            pass
        try:
            # Per-monitor v1 (Windows 8.1+); S_OK == 0
            if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
                return
        except Exception:
            pass
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # system-aware fallback
        except Exception:
            pass
    except Exception:
        pass


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


def enumerate_folder_files(folder_path):
    """Recursively collect every file under folder_path.

    Returns (entries, errors). Each entry is a dict with:
      - "local_path": absolute path of the file on disk
      - "rel": the file path relative to folder_path, using "/" separators
    errors is a list of (path, message) tuples for directories that could
    not be read (e.g. permission problems); the walk continues past them.
    """
    entries = []
    errors = []

    def on_error(err):
        errors.append((getattr(err, "filename", None) or folder_path, str(err)))

    for root, dirs, files in os.walk(folder_path, onerror=on_error):
        dirs.sort()
        for file_name in sorted(files):
            file_path = os.path.join(root, file_name)
            rel = os.path.relpath(file_path, folder_path).replace(os.sep, "/")
            if not rel or rel == "." or rel.startswith("../"):
                continue
            entries.append({"local_path": os.path.abspath(file_path), "rel": rel})
    return entries, errors


SCRAPE_URL = "https://archive.org/services/search/v1/scrape"


def extract_screenname(value):
    """Extract a screenname from a bare name, @name, or profile URL."""
    value = (value or "").strip()
    if not value:
        return ""
    if "://" in value or value.lower().startswith("archive.org/"):
        candidate = value if "://" in value else "https://" + value
        try:
            path = urlparse(candidate).path
        except ValueError:
            return ""
        for part in path.split("/"):
            if part.startswith("@") and len(part) > 1:
                return part[1:].strip()
        return ""
    return value.lstrip("@").strip()


def parse_screennames(raw):
    """Split a raw screenname field (names, @names, or URLs) into names."""
    names = []
    for part in re.split(r"[,\s]+", raw or ""):
        name = extract_screenname(part)
        if name and name not in names:
            names.append(name)
    return names


def scrape_search_items(query):
    """Page through archive.org's search/scrape API for one query.

    Returns (items, error); items are {"identifier", "title"} dicts.
    Note: the endpoint returns 0 items when `fields` does not include
    `title`, so both fields are always requested here.
    """
    items = []
    cursor = None
    for _ in range(50):  # hard cap on pages
        params = {"q": query, "fields": "identifier,title", "count": 10000}
        if cursor:
            params["cursor"] = cursor
        try:
            response = requests.get(SCRAPE_URL, params=params, timeout=(30, 120))
        except requests.RequestException as exc:
            return [], f"Search request failed: {exc}"
        if response.status_code != 200:
            return [], f"Search error: {response.status_code} {response.reason}"
        try:
            data = response.json()
        except ValueError:
            return [], "Search response was not valid JSON."
        if data.get("error"):
            return [], f"Search error: {data['error']}"
        page = data.get("items") or []
        items.extend(
            {"identifier": item.get("identifier", ""), "title": item.get("title", "")}
            for item in page
            if item.get("identifier")
        )
        cursor = data.get("cursor")
        if not cursor or not page:
            break
    items.sort(key=lambda item: item["identifier"].lower())
    return items, None


def build_file_tree_rows(file_paths):
    """Build directory-style rows from flat 'a/b/c' file paths.

    Returns a list of row dicts:
      files:   {"type": "file", "label", "depth", "file_index", "path"}
      folders: {"type": "folder", "label", "depth", "path",
                "file_indices": [flat indices of every contained file],
                "children": [child rows], "collapsed": False}
    Folders are listed before files at each level, both alphabetical.
    """
    root = {"dirs": {}, "files": []}
    for index, path in enumerate(file_paths):
        parts = [part for part in path.split("/") if part]
        if not parts:
            continue
        node = root
        for part in parts[:-1]:
            node = node["dirs"].setdefault(part, {"dirs": {}, "files": []})
        node["files"].append((index, path))

    def build(node, depth, prefix):
        rows = []
        for name in sorted(node["dirs"], key=str.lower):
            child = node["dirs"][name]
            folder_path = f"{prefix}{name}/"
            children = build(child, depth + 1, folder_path)
            file_indices = []
            for row in children:
                if row["type"] == "folder":
                    file_indices.extend(row["file_indices"])
                else:
                    file_indices.append(row["file_index"])
            rows.append(
                {
                    "type": "folder",
                    "label": f"{name}/",
                    "depth": depth,
                    "path": folder_path,
                    "file_indices": file_indices,
                    "children": children,
                    "collapsed": False,
                }
            )
        for index, path in sorted(node["files"], key=lambda item: item[1].lower()):
            name = path.rsplit("/", 1)[-1]
            rows.append(
                {
                    "type": "file",
                    "label": name,
                    "depth": depth,
                    "file_index": index,
                    "path": path,
                }
            )
        return rows

    return build(root, 0, "")


def fetch_account_repositories(names, include_favorites=True):
    """Fetch every repository uploaded by the given account name(s).

    For each name both uploader search forms are queried and merged (union by
    identifier) so items appear no matter which uploader field they carry;
    names containing uppercase that find nothing are retried in lowercase.
    Optionally each account's favorites collection (fav-<name>) is included.
    Returns (items, error) where items are {"identifier", "title", "saved"}
    dicts sorted by identifier.
    """
    merged = {}
    upload_ids = set()
    for name in names:
        variants = [name]
        if name != name.lower():
            variants.append(name.lower())
        for variant in variants:
            found_for_variant = 0
            queries = [
                f"_uploader_screenname:{variant}",
                f"_uploader_useritem:@{variant}",
            ]
            if include_favorites:
                queries.append(f"collection:fav-{variant}")
            for query in queries:
                fetched, error = scrape_search_items(query)
                if error:
                    return [], error
                found_for_variant += len(fetched)
                is_favorites = query.startswith("collection:fav-")
                for item in fetched:
                    identifier = item["identifier"]
                    if not is_favorites:
                        upload_ids.add(identifier)
                    existing = merged.get(identifier)
                    if existing is None:
                        record = dict(item)
                        record["saved"] = is_favorites
                        merged[identifier] = record
                    elif not existing.get("title") and item.get("title"):
                        existing["title"] = item["title"]
            if found_for_variant:
                break
    # Anything actually uploaded by one of the accounts is not tagged
    # [saved], even if another account also favorited it.
    for identifier in upload_ids:
        record = merged.get(identifier)
        if record is not None:
            record["saved"] = False
    items = list(merged.values())
    items.sort(key=lambda item: item["identifier"].lower())
    return items, None


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
        _enable_windows_dpi_awareness()
        super().__init__()
        self._ui_scale = 1.0
        self._configure_scaling()
        # Row counts are DPI-adaptive so the fixed parts of the layout keep
        # fitting as fonts grow with the display scaling.
        self._list_rows = max(6, round(16 / self._ui_scale))
        self._status_rows = max(5, round(10 / self._ui_scale))
        self.title(f"IA Interact GUI v{__version__}")
        self.geometry(f"{int(1040 * self._ui_scale)}x{int(740 * self._ui_scale)}")
        self.minsize(
            min(int(940 * self._ui_scale), int(self.winfo_screenwidth() * 0.95)),
            min(int(640 * self._ui_scale), int(self.winfo_screenheight() * 0.9)),
        )
        self._configure_dark_theme()

        self.access_key = ""
        self.secret_key = ""
        self.current_identifier = ""

        self.remote_files = []
        self.remote_tree_rows = []
        self.remote_visible_rows = []
        self._remote_select_lock = False
        self.local_files = []

        self.access_key_var = tk.StringVar()
        self.secret_key_var = tk.StringVar()
        self.repo_var = tk.StringVar()
        self.target_directory_var = tk.StringVar(value="uploads")
        self.keep_folder_name_var = tk.BooleanVar(value=True)

        self.login_frame = None
        self.main_frame = None
        self.remote_listbox = None
        self.local_listbox = None
        self.local_files_frame = None
        self.dnd_hint_label = None
        self.status_text = None
        self.account_repos = []
        self.account_repos_listbox = None
        self.account_repos_count_label = None
        self._repos_fetch_running = False

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
        self.dnd_enabled = False
        self._dnd_load_failed = False

        self.update_store = ia_update.UpdateStore() if ia_update is not None else None
        self._update_check_lock = threading.Lock()
        self._update_check_running = False
        self._update_check_pending = None

        self._build_menu()
        self._build_login_screen()

    def _configure_scaling(self):
        """Compute the UI pixel scale factor from the display's real DPI.

        Tk derives its font scaling (points -> pixels) from the process DPI
        automatically once the process is DPI-aware (see
        _enable_windows_dpi_awareness); it must NOT be overridden. The old
        implementation set `tk scaling` from winfo_fpixels("1i"), which is
        itself just 72 * the current scaling, so on every start it silently
        multiplied the font scale by 0.75 - all text rendered ~25% smaller
        than in other applications. Window geometry is scaled by this
        factor instead, and fixed row counts are divided by it.
        """
        try:
            dpi = self.winfo_fpixels("1i")
        except tk.TclError:
            dpi = 96.0
        self._ui_scale = max(1.0, min(2.0, dpi / 96.0))

    def _fit_window_to_content(self):
        """Enlarge the window to fit the current content, clamped to the screen."""
        try:
            self.update_idletasks()
            needed_w = self.main_frame.winfo_reqwidth() + 24
            needed_h = self.main_frame.winfo_reqheight() + 48  # + title bar
            max_w = int(self.winfo_screenwidth() * 0.95)
            max_h = int(self.winfo_screenheight() * 0.92)
            width = min(max(self.winfo_width(), needed_w), max_w)
            height = min(max(self.winfo_height(), needed_h), max_h)
            self.geometry(f"{width}x{height}")
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
        style.configure("MutedCard.TLabel", background=self.COLOR_PANEL, foreground=self.COLOR_MUTED)
        style.configure("Muted.TLabel", background=self.COLOR_BG, foreground=self.COLOR_MUTED)
        style.configure("TCheckbutton", background=self.COLOR_BG, foreground=self.COLOR_TEXT)
        style.map(
            "TCheckbutton",
            background=[("active", self.COLOR_BG)],
            foreground=[("disabled", self.COLOR_MUTED)],
        )
        style.configure("Card.TCheckbutton", background=self.COLOR_PANEL, foreground=self.COLOR_TEXT)
        style.map(
            "Card.TCheckbutton",
            background=[("active", self.COLOR_PANEL)],
            foreground=[("disabled", self.COLOR_MUTED)],
        )
        style.configure(
            "Header.TLabel",
            background=self.COLOR_PANEL,
            foreground=self.COLOR_TEXT,
            font=("TkDefaultFont", 16, "bold"),
        )
        style.configure("TEntry", fieldbackground=self.COLOR_ENTRY, foreground=self.COLOR_TEXT, insertcolor=self.COLOR_TEXT)
        style.map("TEntry", fieldbackground=[("disabled", self.COLOR_PANEL_ALT)])
        # Readonly comboboxes (login Saved Profile) default to a light field
        # with light text in clam - unreadable. Give them dark fields.
        style.configure(
            "TCombobox",
            fieldbackground=self.COLOR_ENTRY,
            foreground=self.COLOR_TEXT,
            background=self.COLOR_PANEL_ALT,
            arrowcolor=self.COLOR_TEXT,
            bordercolor=self.COLOR_BORDER,
            lightcolor=self.COLOR_BORDER,
            darkcolor=self.COLOR_BORDER,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", self.COLOR_ENTRY), ("disabled", self.COLOR_PANEL_ALT)],
            foreground=[("readonly", self.COLOR_TEXT), ("disabled", self.COLOR_MUTED)],
            background=[("active", self.COLOR_BORDER)],
        )
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
        # ttk.Combobox popdowns are plain Tk listboxes that ignore the ttk
        # style; theme them via the option database so dropdown items (e.g.
        # the My Repositories picker) are readable and have a visible
        # selection highlight.
        self.option_add("*TCombobox*Listbox.background", self.COLOR_ENTRY)
        self.option_add("*TCombobox*Listbox.foreground", self.COLOR_TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", self.COLOR_ACCENT_ACTIVE)
        self.option_add("*TCombobox*Listbox.selectForeground", self.COLOR_ENTRY)
        self.option_add("*TCombobox*Listbox.justify", "left")

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
        # High-contrast selection: bright blue background with near-black
        # text (far more readable against the dark theme than white on blue).
        widget.configure(
            bg=self.COLOR_ENTRY,
            fg=self.COLOR_TEXT,
            selectbackground=self.COLOR_ACCENT_ACTIVE,
            selectforeground=self.COLOR_ENTRY,
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
        ttk.Button(
            account_bar, text="Browse Repositories", command=self._open_repositories_page
        ).pack(side="right", padx=(0, 8))
        if self.account_store is not None:
            ttk.Button(
                account_bar, text="Accounts", command=self._open_main_account_manager
            ).pack(side="right")

        repo_frame = ttk.LabelFrame(self.main_frame, text="Repository", padding=10, style="Card.TLabelframe")
        repo_frame.pack(fill="x")
        ttk.Label(repo_frame, text="Repository Link or Identifier", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(repo_frame, textvariable=self.repo_var, width=80).grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(4, 0))
        ttk.Button(repo_frame, text="Load Repository Files", command=self.load_repository_files, style="Accent.TButton").grid(row=1, column=1, sticky="ew", pady=(4, 0))

        ttk.Label(
            repo_frame,
            text="My Repositories (this account's uploads - double-click to open)",
            style="Card.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Button(repo_frame, text="Refresh", command=self.refresh_account_repositories).grid(row=2, column=1, sticky="ew", pady=(8, 0))
        self.account_repos_listbox = tk.Listbox(
            repo_frame,
            selectmode=tk.BROWSE,
            activestyle="none",
            exportselection=False,
            height=6,
        )
        self._apply_dark_text_widget_theme(self.account_repos_listbox)
        self.account_repos_listbox.bind("<Double-Button-1>", self._use_account_repository)
        self.account_repos_listbox.grid(row=3, column=0, sticky="ew", padx=(0, 8), pady=(4, 0))
        repos_scroll = ttk.Scrollbar(repo_frame, orient="vertical", command=self.account_repos_listbox.yview)
        self.account_repos_listbox.configure(yscrollcommand=repos_scroll.set)
        repos_scroll.grid(row=3, column=1, sticky="ns")
        self.account_repos_count_label = ttk.Label(repo_frame, text="", style="MutedCard.TLabel")
        self.account_repos_count_label.grid(row=4, column=0, columnspan=2, sticky="w")
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
            height=self._list_rows,
        )
        self._apply_dark_text_widget_theme(self.remote_listbox)
        self.remote_listbox.bind("<<ListboxSelect>>", self._on_remote_select)
        self.remote_listbox.bind("<Double-Button-1>", self._toggle_remote_folder)
        self.remote_listbox.bind("<Button-3>", self._show_remote_context_menu)
        remote_scroll = ttk.Scrollbar(remote_frame, orient="vertical", command=self.remote_listbox.yview)
        remote_scroll_x = ttk.Scrollbar(remote_frame, orient="horizontal", command=self.remote_listbox.xview)
        self.remote_listbox.configure(yscrollcommand=remote_scroll.set, xscrollcommand=remote_scroll_x.set)
        self.remote_listbox.grid(row=0, column=0, sticky="nsew")
        remote_scroll.grid(row=0, column=1, sticky="ns")
        remote_scroll_x.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        local_frame = ttk.LabelFrame(list_container, text="Local Files (select for upload)", padding=8, style="Card.TLabelframe")
        local_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.local_files_frame = local_frame
        local_frame.columnconfigure(0, weight=1)
        local_frame.rowconfigure(0, weight=1)

        self.local_listbox = tk.Listbox(
            local_frame,
            selectmode=tk.EXTENDED,
            exportselection=False,
            activestyle="none",
            height=self._list_rows,
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
        ttk.Button(local_buttons, text="Add Folder", command=self.select_local_folder).pack(side="left", padx=8)
        ttk.Button(local_buttons, text="Remove Selected", command=self.remove_selected_local_files).pack(side="left")
        ttk.Button(local_buttons, text="Clear", command=self.clear_local_files).pack(side="left", padx=8)

        local_options = ttk.Frame(local_frame, style="Card.TFrame")
        local_options.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Checkbutton(
            local_options,
            text="Keep folder name in upload path",
            variable=self.keep_folder_name_var,
            command=self._on_keep_folder_name_toggled,
            style="Card.TCheckbutton",
        ).pack(side="left")

        self.dnd_hint_label = ttk.Label(local_frame, text="", style="MutedCard.TLabel")
        self.dnd_hint_label.grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))

        action_frame = ttk.LabelFrame(self.main_frame, text="Actions", padding=10, style="Card.TLabelframe")
        action_frame.pack(fill="x")
        action_frame.columnconfigure(1, weight=1)
        ttk.Label(action_frame, text="Target upload directory", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(action_frame, textvariable=self.target_directory_var).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        self.upload_button = ttk.Button(action_frame, text="Upload Local Files", command=self.upload_selected_local_files, style="Accent.TButton")
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

        self.status_text = ScrolledText(status_frame, height=self._status_rows, wrap="word", state="disabled", padx=8, pady=8)
        self._apply_dark_text_widget_theme(self.status_text)
        self.status_text.grid(row=0, column=0, sticky="nsew")

        # Enable drag & drop (optional tkinterdnd2) now that the local files
        # panel exists.
        self._set_dnd_hint(self._configure_dnd())
        if self.dnd_enabled:
            self.append_status(
                "Drag & drop enabled: drop files or folders onto the Local Files panel."
            )

        # Grow the window if the DPI-scaled content needs more room than the
        # default geometry provides (never beyond the screen).
        self._fit_window_to_content()

        # Auto-list this account's repositories from the profile's saved IA
        # screenname (no typing needed).
        self.refresh_account_repositories()

        # Defer the automatic background update check until the main screen
        # (and its status log) exist so any "update available" notice has
        # somewhere to land. Runs at most once every 7 days (see ia_update).
        self.after(2000, self._maybe_auto_check)

    def _make_dark_menu(self, parent=None):
        """A tk.Menu with explicit dark colors (tk menus ignore the ttk style)."""
        return tk.Menu(
            parent or self,
            tearoff=0,
            bg=self.COLOR_PANEL,
            fg=self.COLOR_TEXT,
            activebackground=self.COLOR_ACCENT,
            activeforeground="#ffffff",
            disabledforeground=self.COLOR_MUTED,
        )

    def _build_menu(self):
        menubar = self._make_dark_menu()
        self.menubar = menubar
        help_menu = self._make_dark_menu(menubar)
        help_menu.add_command(label="Check for Updates...", command=self._open_update_dialog)
        help_menu.add_separator()
        help_menu.add_command(label="About IA Interact", command=self._open_about_dialog)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.configure(menu=menubar)

    def _open_about_dialog(self):
        dialog = tk.Toplevel(self)
        dialog.title("About IA Interact")
        dialog.configure(bg=self.COLOR_BG)
        dialog.transient(self)
        dialog.resizable(False, False)

        body = ttk.Frame(dialog, padding=20, style="TFrame")
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="IA Interact", style="Header.TLabel").pack(anchor="w")
        ttk.Label(body, text=f"Version {__version__}", style="Card.TLabel").pack(anchor="w", pady=(4, 12))

        repo_url = (
            ia_update.RELEASES_LATEST_URL
            if ia_update is not None
            else "https://github.com/harrypm/IA-Interact"
        )
        info = (
            "A small desktop client for interacting with Internet Archive\n"
            "repositories (upload, download, list, delete, move).\n\n"
            f"Source / releases:\n{repo_url}\n\n"
            "S3 credentials are stored locally only; see README for details."
        )
        ttk.Label(body, text=info, style="Card.TLabel", justify="left").pack(anchor="w")

        ttk.Button(body, text="Close", command=dialog.destroy, style="Accent.TButton").pack(anchor="e", pady=(16, 0))

        try:
            dialog.wait_visibility()
        except tk.TclError:
            pass
        try:
            dialog.grab_set()
        except tk.TclError:
            pass
        try:
            dialog.focus_set()
        except tk.TclError:
            pass

    def _open_update_dialog(self):
        UpdateDialog(self)

    def _maybe_auto_check(self):
        if ia_update is None or self.update_store is None:
            return
        try:
            due = self.update_store.is_due()
        except Exception:
            due = False
        if due:
            self._run_update_check(manual=False)

    @staticmethod
    def _update_result_error(message):
        return {"ok": False, "tag": "", "available": False, "cmp": None, "error": message}

    def _run_update_check(self, manual, on_result=None):
        """Run a single update check on a daemon thread.

        Shared by the automatic background check (manual=False) and the Update
        dialog (manual=True). Guarded so only one check runs at a time.

        Thread safety: the worker thread NEVER touches Tk. It only computes the
        result (ia_update.check_for_update) and stashes it in
        ``self._update_check_pending`` under the lock. A main-thread poller
        (``_poll_update_check``, scheduled via ``after`` from here) drains the
        pending result and does all UI/store work on the Tk thread. This avoids
        the ``RuntimeError: main thread is not in main loop`` that calling
        ``after``/widget methods from a background thread can raise.
        """
        if ia_update is None or self.update_store is None:
            if on_result is not None:
                on_result(self._update_result_error("Update checker unavailable."))
            return

        with self._update_check_lock:
            if self._update_check_running:
                if on_result is not None:
                    on_result(self._update_result_error("An update check is already running."))
                return
            self._update_check_running = True
            self._update_check_pending = None

        def worker():
            result = ia_update.check_for_update(__version__)
            with self._update_check_lock:
                self._update_check_pending = (result, manual, on_result)
                self._update_check_running = False

        threading.Thread(target=worker, daemon=True).start()
        self.after(50, self._poll_update_check)

    def _poll_update_check(self):
        """Main-thread poller: drain a completed update check and finish it on the Tk thread."""
        with self._update_check_lock:
            pending = self._update_check_pending
            self._update_check_pending = None
            running = self._update_check_running
        if pending is not None:
            result, manual, on_result = pending
            self._on_update_check_done(result, manual, on_result)
            return
        if running:
            self.after(50, self._poll_update_check)

    def _on_update_check_done(self, result, manual, on_result):
        # Persist the result + cached availability on the Tk thread (no race with reads).
        if result.get("ok") and self.update_store is not None:
            try:
                self.update_store.record(result["tag"], __version__)
            except Exception:
                pass
        if on_result is not None:
            try:
                on_result(result)
            except Exception:
                pass
        # Silent auto-check: only surface a notice when an update is actually
        # available and the status log exists. Manual results are shown by the dialog.
        if (
            not manual
            and self.status_text is not None
            and result.get("ok")
            and result.get("available")
        ):
            self.append_status(
                f"Update available: {result['tag']} (current v{__version__}). "
                "See Help > Check for Updates."
            )

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

    def _open_repositories_page(self):
        RepositoriesDialog(self)

    def _account_screennames(self):
        """Screennames saved on the active account profile (may be several)."""
        if self.account_store is None or not self.current_profile:
            return []
        try:
            profile = self.account_store.get_profile(self.current_profile)
        except Exception:
            profile = None
        if not profile:
            return []
        return parse_screennames(profile.get("screenname") or "")

    def refresh_account_repositories(self):
        """Load the account's repositories into the main-window picker."""
        if self._repos_fetch_running:
            return
        names = self._account_screennames()
        if not names:
            self.account_repos = []
            if self.account_repos_listbox is not None:
                self.account_repos_listbox.delete(0, "end")
            if self.account_repos_count_label is not None:
                self.account_repos_count_label.configure(
                    text="No IA screenname set for this account (add one in Accounts)."
                )
            return
        self._repos_fetch_running = True
        if self.account_repos_count_label is not None:
            self.account_repos_count_label.configure(text="Loading account repositories...")
        names_label = ", ".join(names)

        def worker():
            items, error = fetch_account_repositories(names, include_favorites=True)

            def done():
                self._repos_fetch_running = False
                if self.account_repos_count_label is not None:
                    self.account_repos_count_label.configure(text="")
                if error:
                    self.append_status(f"Account repositories load failed: {error}")
                    return
                self.account_repos = items
                if self.account_repos_listbox is not None:
                    self.account_repos_listbox.delete(0, "end")
                    for item in items:
                        title = item["title"] or ""
                        line = f"{item['identifier']}    {title}" if title else item["identifier"]
                        if item.get("saved"):
                            line += "  [saved]"
                        self.account_repos_listbox.insert("end", line)
                if self.account_repos_count_label is not None:
                    uploads = sum(1 for item in items if not item.get("saved"))
                    saved = len(items) - uploads
                    text = f"{len(items)} repositories for {names_label}"
                    if saved:
                        text += f" ({uploads} uploaded, {saved} saved)"
                    self.account_repos_count_label.configure(text=text)

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _use_account_repository(self, event=None):
        """Load the repository picked in the account repositories list."""
        if self.account_repos_listbox is None:
            return
        selection = list(self.account_repos_listbox.curselection())
        if not selection:
            return
        index = selection[0]
        if not 0 <= index < len(self.account_repos):
            return
        item = self.account_repos[index]
        identifier = item["identifier"]
        self.repo_var.set(identifier)
        self.append_status(
            f"Repository '{identifier}' selected from account list; loading files..."
        )
        self.load_repository_files()

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
        self.refresh_account_repositories()
        return True

    def _set_remote_files(self, files):
        self.remote_files = files
        self.remote_tree_rows = build_file_tree_rows(files)
        self._render_remote_rows()

    def _render_remote_rows(self):
        """Draw the repository file tree (folders first, contents indented)."""
        self.remote_listbox.delete(0, "end")
        visible = []

        def collect(rows):
            for row in rows:
                visible.append(row)
                if row["type"] == "folder" and not row.get("collapsed"):
                    collect(row["children"])

        collect(self.remote_tree_rows)
        self.remote_visible_rows = visible
        for row in visible:
            indent = "    " * row["depth"]
            if row["type"] == "folder":
                count = len(row["file_indices"])
                plural = "s" if count != 1 else ""
                self.remote_listbox.insert("end", f"{indent}{row['label']}  ({count} file{plural})")
            else:
                self.remote_listbox.insert("end", f"{indent}{row['label']}")

    def _on_remote_select(self, event=None):
        """Selecting a folder row also selects every file it contains."""
        if self._remote_select_lock:
            return
        selection = list(self.remote_listbox.curselection())
        if not selection:
            return
        folder_file_sets = []
        for index in selection:
            if 0 <= index < len(self.remote_visible_rows):
                row = self.remote_visible_rows[index]
                if row["type"] == "folder":
                    folder_file_sets.append(set(row["file_indices"]))
        if not folder_file_sets:
            return
        extra = []
        for visible_index, visible_row in enumerate(self.remote_visible_rows):
            if visible_row["type"] == "file":
                for file_set in folder_file_sets:
                    if visible_row["file_index"] in file_set:
                        extra.append(visible_index)
                        break
            elif visible_row["file_indices"]:
                # nested folder rows inside a selected folder are selected too
                nested_set = set(visible_row["file_indices"])
                for file_set in folder_file_sets:
                    if nested_set <= file_set:
                        extra.append(visible_index)
                        break
        if not extra:
            return
        self._remote_select_lock = True
        try:
            for visible_index in extra:
                self.remote_listbox.selection_set(visible_index)
        finally:
            self._remote_select_lock = False

    def _toggle_remote_folder(self, event=None):
        """Double-click a folder row to collapse/expand it."""
        selection = list(self.remote_listbox.curselection())
        if not selection:
            return
        index = selection[0]
        if not 0 <= index < len(self.remote_visible_rows):
            return
        row = self.remote_visible_rows[index]
        if row["type"] != "folder":
            return
        row["collapsed"] = not row.get("collapsed")
        self._render_remote_rows()

    def _remote_row_at_y(self, y):
        """Row index under a click y-position (None if the list is empty)."""
        if not self.remote_visible_rows:
            return None
        index = self.remote_listbox.nearest(y)
        if 0 <= index < len(self.remote_visible_rows):
            return index
        return None

    def _show_remote_context_menu(self, event):
        """Right-click menu on the repository file tree: quick downloads."""
        index = self._remote_row_at_y(event.y)
        if index is not None and index not in self.remote_listbox.curselection():
            self.remote_listbox.selection_clear(0, "end")
            self.remote_listbox.selection_set(index)
            self._on_remote_select()
        menu = self._build_remote_context_menu(index)
        if menu is None:
            return
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _build_remote_context_menu(self, clicked_index=None):
        """Build the right-click download menu.

        The primary entry targets the clicked row (file or whole folder);
        "Download Selected" is offered when the selection contains files
        beyond that target.
        """
        if not self.remote_files:
            return None
        selection = list(self.remote_listbox.curselection())
        if not selection and clicked_index is None:
            return None
        menu = self._make_dark_menu()
        primary_index = None
        if clicked_index is not None and 0 <= clicked_index < len(self.remote_visible_rows):
            primary_index = clicked_index
        elif len(selection) == 1 and 0 <= selection[0] < len(self.remote_visible_rows):
            primary_index = selection[0]
        if primary_index is not None:
            row = self.remote_visible_rows[primary_index]
            if row["type"] == "folder":
                menu.add_command(
                    label="Download This Folder",
                    command=lambda: self._download_remote_row(primary_index),
                )
            else:
                menu.add_command(
                    label="Download This File",
                    command=lambda: self._download_remote_row(primary_index),
                )
        selected_files = self._selected_remote_files()
        if selection and selected_files:
            target_paths = set()
            if primary_index is not None:
                row = self.remote_visible_rows[primary_index]
                if row["type"] == "folder":
                    target_paths = {self.remote_files[i] for i in row["file_indices"]}
                else:
                    target_paths = {self.remote_files[row["file_index"]]}
            if any(path not in target_paths for path in selected_files):
                count = len(selected_files)
                plural = "s" if count != 1 else ""
                if menu.index("end") is not None:
                    menu.add_separator()
                menu.add_command(
                    label=f"Download Selected ({count} file{plural})",
                    command=self.download_selected_repository_files,
                )
        return menu

    def _download_remote_row(self, index):
        """Right-click action: download exactly the file/folder at this row."""
        self.remote_listbox.selection_clear(0, "end")
        self.remote_listbox.selection_set(index)
        self._on_remote_select()
        self.download_selected_repository_files()

    def _selected_remote_files(self):
        """Flat file list for the current selection.

        Folder rows contribute every file they contain - including files
        hidden inside a collapsed folder.
        """
        selected = []
        seen = set()
        for index in self.remote_listbox.curselection():
            if not 0 <= index < len(self.remote_visible_rows):
                continue
            row = self.remote_visible_rows[index]
            if row["type"] == "file":
                if row["file_index"] not in seen:
                    seen.add(row["file_index"])
                    selected.append(self.remote_files[row["file_index"]])
            else:
                for file_index in row["file_indices"]:
                    if file_index not in seen:
                        seen.add(file_index)
                        selected.append(self.remote_files[file_index])
        return selected

    def _refresh_local_files(self):
        self.local_listbox.delete(0, "end")
        for entry in self.local_files:
            if entry.get("folder_root"):
                display = f"{self._entry_remote_rel(entry)}    [{entry['local_path']}]"
            else:
                display = entry["local_path"]
            self.local_listbox.insert("end", display)

    def _entry_remote_rel(self, entry):
        """Repository-side path for a list entry, relative to the target directory."""
        folder_root = entry.get("folder_root")
        if folder_root and self.keep_folder_name_var.get():
            return f"{folder_root}/{entry['rel']}"
        return entry["rel"]

    def _on_keep_folder_name_toggled(self):
        self._refresh_local_files()

    def _existing_local_paths(self):
        return {entry["local_path"] for entry in self.local_files}

    def _add_file_entries(self, file_paths, existing):
        """Append individually selected files; returns how many were added."""
        added = 0
        for file_path in file_paths:
            file_path = os.path.normpath(file_path)
            if file_path in existing:
                continue
            existing.add(file_path)
            self.local_files.append(
                {"local_path": file_path, "rel": os.path.basename(file_path), "folder_root": None}
            )
            added += 1
        return added

    def _add_folder_entries(self, folder_path, existing):
        """Recursively append every file under folder_path; returns how many were added."""
        folder_path = os.path.abspath(folder_path)
        entries, errors = enumerate_folder_files(folder_path)
        if not entries and not errors:
            self.append_status(f"No files found in folder: {folder_path}")
            return 0
        root_name = os.path.basename(os.path.normpath(folder_path))
        added = 0
        for entry in entries:
            if entry["local_path"] in existing:
                continue
            existing.add(entry["local_path"])
            entry["folder_root"] = root_name or None
            self.local_files.append(entry)
            added += 1
        if errors:
            first_path, first_message = errors[0]
            self.append_status(
                f"Warning: {len(errors)} subfolder(s) under '{folder_path}' could not be read "
                f"(first: {first_path}: {first_message})."
            )
        return added

    def _warn_duplicate_targets(self):
        seen = set()
        duplicates = set()
        for entry in self.local_files:
            target = self._entry_remote_rel(entry)
            if target in seen:
                duplicates.add(target)
            seen.add(target)
        if duplicates:
            self.append_status(
                "Warning: duplicate upload target name(s): "
                + ", ".join(sorted(duplicates))
                + ". Files uploading to the same repository path overwrite each other."
            )

    def select_local_files(self):
        file_paths = filedialog.askopenfilenames(title="Select files to upload")
        if not file_paths:
            return

        existing = self._existing_local_paths()
        added = self._add_file_entries(file_paths, existing)

        self._refresh_local_files()
        if added:
            self.append_status(f"Added {added} file(s) to upload list.")
        else:
            self.append_status("No new files added (selected file(s) were already in the list).")
        self._warn_duplicate_targets()

    def select_local_folder(self):
        folder_path = filedialog.askdirectory(title="Select a folder to upload")
        if not folder_path:
            return

        existing = self._existing_local_paths()
        added = self._add_folder_entries(folder_path, existing)

        self._refresh_local_files()
        if added:
            structure = (
                "structure kept under the folder's name"
                if self.keep_folder_name_var.get()
                else "structure kept, folder name excluded"
            )
            self.append_status(f"Added {added} file(s) from folder '{folder_path}' ({structure}).")
        self._warn_duplicate_targets()

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

    def _configure_dnd(self):
        """Register the Local Files panel as a drag & drop target.

        Uses the optional tkinterdnd2 package (tkdnd Tcl extension). Returns
        True when drag & drop is active; the GUI works normally without it.
        """
        if TkinterDnD is None or self._dnd_load_failed:
            return False
        if getattr(self, "TkdndVersion", None) is None:
            try:
                self.TkdndVersion = TkinterDnD.require(self)
            except (RuntimeError, tk.TclError):
                self._dnd_load_failed = True
                return False
        try:
            for widget in (self.local_listbox, self.local_files_frame):
                if widget is None:
                    continue
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_dnd_drop)
        except (tk.TclError, AttributeError):
            return False
        self.dnd_enabled = True
        return True

    def _set_dnd_hint(self, enabled):
        if self.dnd_hint_label is None:
            return
        if enabled:
            text = "Drag & drop: drop files or folders here to add them"
        elif TkinterDnD is None:
            text = "Drag & drop unavailable (optional: pip install tkinterdnd2)"
        else:
            text = "Drag & drop unavailable (tkdnd failed to load)"
        self.dnd_hint_label.configure(text=text)

    @staticmethod
    def _normalize_dnd_path(path):
        path = path.strip()
        if path.startswith("{") and path.endswith("}"):
            path = path[1:-1]
        if path.startswith("file://"):
            path = url2pathname(unquote(urlparse(path).path))
        return os.path.normpath(path)

    def _on_dnd_drop(self, event):
        """<<Drop>> handler: adds dropped files and folders to the upload list."""
        try:
            raw_paths = list(self.tk.splitlist(event.data))
        except (tk.TclError, ValueError):
            raw_paths = [event.data]

        paths = [self._normalize_dnd_path(raw) for raw in raw_paths if raw and raw.strip()]
        if not paths:
            return event.action

        existing = self._existing_local_paths()
        direct_added = 0
        folder_added = 0
        folder_count = 0
        for path in paths:
            if os.path.isdir(path):
                added = self._add_folder_entries(path, existing)
                if added:
                    folder_added += added
                    folder_count += 1
            elif os.path.isfile(path):
                direct_added += self._add_file_entries([path], existing)
            else:
                self.append_status(f"Skipped dropped item (not a file or folder): {path}")

        total = direct_added + folder_added
        if total:
            self._refresh_local_files()
            self.append_status(
                f"Added {total} file(s) to upload list via drag & drop "
                f"({direct_added} file(s), {folder_added} from {folder_count} folder(s))."
            )
        self._warn_duplicate_targets()
        return event.action

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
        # Resolve remote paths on the Tk thread before the worker starts so
        # the upload thread never reads Tk variables.
        files_to_upload = [
            (entry["local_path"], self._entry_remote_rel(entry))
            for entry in self.local_files
        ]

        self.append_status(
            f"Starting upload of {len(files_to_upload)} file(s) to '{identifier}' "
            f"directory '{target_directory or '(root)'}'."
        )

        def worker():
            reporter = _ProgressReporter(self, len(files_to_upload), "upload")
            success_count = 0
            for index, (file_path, remote_rel) in enumerate(files_to_upload, start=1):
                if self.cancel_event.is_set():
                    self.append_status("Upload cancelled by user.")
                    break
                ok, detail = self.upload_single_file(identifier, file_path, remote_rel, target_directory, reporter, index)
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

    def upload_single_file(self, identifier, file_path, object_rel_path, directory, reporter=None, file_index=1):
        """Upload one local file to the repository.

        object_rel_path is the repository-side path (relative to the target
        directory); it may contain '/'-separated subdirectories for files
        that were added from a folder.
        """
        if not os.path.isfile(file_path):
            return False, f"Skipped missing file: {file_path}"

        object_path = f"{directory}/{object_rel_path}" if directory else object_rel_path
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
            reporter.start_file(file_index, object_rel_path, total)

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
            return False, f"Upload failed for '{file_path}' -> '{object_path}': {response.status_code} {response.reason}"

        return True, f"Uploaded: {file_path} -> {object_path}"

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

        selected_files = self._selected_remote_files()
        if not selected_files:
            messagebox.showerror(
                "No files selected",
                "Select one or more repository files (or a whole folder) to download.",
            )
            return

        destination_dir = filedialog.askdirectory(title="Select destination folder")
        if not destination_dir:
            return
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

        scale = getattr(parent, "_ui_scale", 1.0)
        self.title("Account Manager")
        self.geometry(f"{int(660 * scale)}x{int(470 * scale)}")
        self.minsize(int(580 * scale), int(400 * scale))
        self.configure(bg=IAInteractGUI.COLOR_BG)
        self.transient(parent)

        self.name_var = tk.StringVar()
        self.access_var = tk.StringVar()
        self.secret_var = tk.StringVar()
        self.screenname_var = tk.StringVar()
        self.notes_var = tk.StringVar()
        self.show_secret_var = tk.BooleanVar(value=False)

        self._build_ui()
        self._refresh_list()
        try:
            self.wait_visibility()
        except tk.TclError:
            pass
        try:
            self.grab_set()
        except tk.TclError:
            pass
        try:
            self.focus_set()
        except tk.TclError:
            pass

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
        ttk.Checkbutton(form_frame, text="Show", variable=self.show_secret_var, command=self._toggle_secret, style="Card.TCheckbutton").grid(
            row=2, column=2, padx=(8, 0), pady=(0, 6)
        )

        ttk.Label(form_frame, text="IA screenname (optional)", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=(0, 6))
        ttk.Entry(form_frame, textvariable=self.screenname_var).grid(row=3, column=1, columnspan=2, sticky="ew", pady=(0, 6))

        ttk.Label(form_frame, text="Notes", style="Card.TLabel").grid(row=4, column=0, sticky="w", pady=(0, 6))
        ttk.Entry(form_frame, textvariable=self.notes_var).grid(row=4, column=1, columnspan=2, sticky="ew", pady=(0, 6))

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
            selectbackground=IAInteractGUI.COLOR_ACCENT_ACTIVE,
            selectforeground=IAInteractGUI.COLOR_ENTRY,
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
            self.screenname_var.set(profile.get("screenname", ""))
            self.notes_var.set(profile.get("notes", ""))

    def _on_list_double(self, event=None):
        if self.mode == "switch" and self.switch_callback is not None:
            self._use()

    def _clear_form(self):
        self.name_var.set("")
        self.access_var.set("")
        self.secret_var.set("")
        self.screenname_var.set("")
        self.notes_var.set("")
        self.selected_name = None

    def _add(self):
        name = self.name_var.get().strip()
        access = self.access_var.get().strip()
        secret = self.secret_var.get().strip()
        notes = self.notes_var.get().strip()
        screenname = self.screenname_var.get().strip()
        if not name or not access or not secret:
            messagebox.showerror("Missing fields", "Profile name, access key, and secret key are required.")
            return
        try:
            self.store.add_profile(name, access, secret, notes, screenname=screenname)
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
        screenname = self.screenname_var.get().strip()
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
                screenname=screenname,
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


class RepositoriesDialog(tk.Toplevel):
    """Account profile page: lists every repository (item) uploaded by an account.

    Uses archive.org's public search API (no credentials required): items are
    looked up via both the screenname (_uploader_screenname) and user-item
    (_uploader_useritem) forms and merged, so all uploads are listed no matter
    which uploader field they carry. One or more screennames can be entered
    (comma separated; @names and profile URLs are parsed too), and items the
    account saved (favorites) can be included. Selecting a repository loads it
    straight into the main window.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.parent_gui = parent
        self.title("My Repositories")
        scale = getattr(parent, "_ui_scale", 1.0)
        self.geometry(f"{int(780 * scale)}x{int(560 * scale)}")
        self.minsize(int(620 * scale), int(440 * scale))
        self.configure(bg=IAInteractGUI.COLOR_BG)
        self.transient(parent)

        self.repositories = []  # list of {"identifier", "title"}
        self.filtered = []
        self._fetch_running = False

        self.screenname_var = tk.StringVar(value=self._initial_screenname())
        self.filter_var = tk.StringVar()
        self.include_favorites_var = tk.BooleanVar(value=True)
        self.info_var = tk.StringVar(
            value="Enter one or more archive.org screennames (case-sensitive) and click Load."
        )

        self._build_ui()
        try:
            self.wait_visibility()
        except tk.TclError:
            pass
        try:
            self.grab_set()
        except tk.TclError:
            pass
        try:
            self.focus_set()
        except tk.TclError:
            pass
        if self.screenname_var.get():
            self.after(150, self._start_fetch)

    def _initial_screenname(self):
        gui = self.parent_gui
        if gui.account_store is not None and gui.current_profile:
            try:
                profile = gui.account_store.get_profile(gui.current_profile)
            except Exception:
                profile = None
            if profile:
                return (profile.get("screenname") or "").strip()
        return ""

    def _build_ui(self):
        root = ttk.Frame(self, padding=12, style="TFrame")
        root.pack(fill="both", expand=True)

        header = f"Account Repositories — {self.parent_gui.current_profile or 'Manual (no profile)'}"
        ttk.Label(root, text=header, style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            root,
            text="Every archive.org item uploaded by these accounts. Enter one or more "
            "screennames (comma separated, case-sensitive); profile URLs also work.",
            style="Muted.TLabel",
            wraplength=int(740 * getattr(self.parent_gui, "_ui_scale", 1.0)),
            justify="left",
        ).pack(anchor="w", fill="x", pady=(2, 8))

        name_row = ttk.Frame(root, style="TFrame")
        name_row.pack(fill="x", pady=(0, 6))
        ttk.Label(name_row, text="IA screenname(s)", style="TLabel").pack(side="left")
        ttk.Entry(name_row, textvariable=self.screenname_var).pack(
            side="left", fill="x", expand=True, padx=8
        )
        self.load_button = ttk.Button(
            name_row, text="Load", command=self._start_fetch, style="Accent.TButton"
        )
        self.load_button.pack(side="left")
        ttk.Checkbutton(
            name_row, text="Include saved items", variable=self.include_favorites_var
        ).pack(side="left", padx=(8, 0))

        filter_row = ttk.Frame(root, style="TFrame")
        filter_row.pack(fill="x", pady=(0, 6))
        ttk.Label(filter_row, text="Filter", style="TLabel").pack(side="left")
        ttk.Entry(filter_row, textvariable=self.filter_var).pack(
            side="left", fill="x", expand=True, padx=8
        )
        self.filter_var.trace_add("write", lambda *_: self._apply_filter())

        list_frame = ttk.Frame(root, style="Card.TFrame")
        list_frame.pack(fill="both", expand=True)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.repo_listbox = tk.Listbox(
            list_frame, selectmode=tk.EXTENDED, activestyle="none", exportselection=False
        )
        AccountManagerDialog._apply_listbox_theme(self.repo_listbox)
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.repo_listbox.yview)
        self.repo_listbox.configure(yscrollcommand=scroll.set)
        self.repo_listbox.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.repo_listbox.bind("<Double-Button-1>", lambda event: self._use_selected())

        ttk.Label(root, textvariable=self.info_var, style="Muted.TLabel").pack(
            anchor="w", fill="x", pady=(6, 0)
        )

        buttons = ttk.Frame(root, style="TFrame")
        buttons.pack(fill="x", pady=(8, 0))
        self.use_button = ttk.Button(
            buttons, text="Use Selected", command=self._use_selected, style="Accent.TButton"
        )
        self.use_button.pack(side="left")
        ttk.Button(buttons, text="Open in Browser", command=self._open_in_browser).pack(
            side="left", padx=8
        )
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side="right")

    def _start_fetch(self):
        names = parse_screennames(self.screenname_var.get())
        if not names:
            self.info_var.set("Enter at least one archive.org screenname (case-sensitive).")
            return
        if self._fetch_running:
            return
        names_label = ", ".join(names)
        include_favorites = self.include_favorites_var.get()
        self._fetch_running = True
        self.load_button.configure(state="disabled")
        self.use_button.configure(state="disabled")
        self.info_var.set(f"Loading repositories for '{names_label}'...")
        self.repositories = []
        self._apply_filter()

        # Shared fetch logic (both uploader forms per name, union, lowercase
        # retry, optional favorites) lives in fetch_account_repositories().
        def worker():
            items, error = fetch_account_repositories(names, include_favorites)
            self.after(0, lambda: self._fetch_done(names_label, items, error))

        threading.Thread(target=worker, daemon=True).start()

    def _fetch_done(self, names_label, items, error):
        self._fetch_running = False
        self.load_button.configure(state="normal")
        self.use_button.configure(state="normal")
        if error:
            self.info_var.set(f"Failed: {error}")
            self.parent_gui.append_status(f"Repository listing failed: {error}")
            return
        self.repositories = items
        self._apply_filter()
        if items:
            uploads = sum(1 for item in items if not item.get("saved"))
            saved = len(items) - uploads
            summary = f"{len(items)} repositories found for '{names_label}'"
            if saved:
                summary += f" ({uploads} uploaded, {saved} saved)"
            self.info_var.set(summary + ".")
            self.parent_gui.append_status(f"Account repositories: {summary}.")
            self._save_screenname_to_profile(names_label)
        else:
            self.info_var.set(
                f"No repositories found for '{names_label}'. Check the screenname(s) - they "
                "are case-sensitive and must match the name in the archive.org profile URL "
                "(archive.org/details/@name). Separate multiple names with commas."
            )

    def _save_screenname_to_profile(self, screenname):
        gui = self.parent_gui
        if gui.account_store is None or not gui.current_profile:
            return
        try:
            profile = gui.account_store.get_profile(gui.current_profile)
        except Exception:
            return
        if not profile or (profile.get("screenname") or "") == screenname:
            return
        try:
            gui.account_store.update_profile(gui.current_profile, screenname=screenname)
            gui.append_status(
                f"Saved IA screenname '{screenname}' to profile '{gui.current_profile}'."
            )
            gui.refresh_account_repositories()
        except Exception:
            pass

    def _apply_filter(self):
        term = self.filter_var.get().strip().lower()
        if term:
            self.filtered = [
                item
                for item in self.repositories
                if term in item["identifier"].lower()
                or term in (item["title"] or "").lower()
            ]
        else:
            self.filtered = list(self.repositories)
        self.repo_listbox.delete(0, "end")
        for item in self.filtered:
            title = item["title"] or ""
            line = f"{item['identifier']}    {title}" if title else item["identifier"]
            if item.get("saved"):
                line += "  [saved]"
            self.repo_listbox.insert("end", line)

    def _selected_repository(self):
        selection = self.repo_listbox.curselection()
        if not selection:
            return None
        index = selection[0]
        if 0 <= index < len(self.filtered):
            return self.filtered[index]
        return None

    def _use_selected(self):
        item = self._selected_repository()
        if not item:
            self.info_var.set("Select a repository from the list first.")
            return
        identifier = item["identifier"]
        gui = self.parent_gui
        gui.repo_var.set(identifier)
        gui.append_status(
            f"Repository '{identifier}' selected from account list; loading files..."
        )
        gui.load_repository_files()
        self.destroy()

    def _open_in_browser(self):
        item = self._selected_repository()
        if not item:
            self.info_var.set("Select a repository from the list first.")
            return
        try:
            webbrowser.open(f"https://archive.org/details/{item['identifier']}")
        except Exception as exc:
            messagebox.showerror("Browser", f"Failed to open browser:\n{exc}")


class UpdateDialog(tk.Toplevel):
    """Dialog showing update status with Check Now / Download Latest / Close.

    Opens with the cached last-check state, then kicks a fresh check if there
    is no cached result yet. Mirrors MISRC-GUI's version-info update row.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        scale = getattr(parent, "_ui_scale", 1.0)
        self.title("Check for Updates")
        self.geometry(f"{int(460 * scale)}x{int(280 * scale)}")
        self.minsize(int(400 * scale), int(240 * scale))
        self.configure(bg=IAInteractGUI.COLOR_BG)
        self.transient(parent)

        self._latest_tag = None

        body = ttk.Frame(self, padding=16, style="TFrame")
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="Check for Updates", style="Header.TLabel").pack(anchor="w")
        ttk.Label(body, text=f"Installed version: v{__version__}", style="Card.TLabel").pack(anchor="w", pady=(4, 8))

        self.status_label = ttk.Label(body, text="", style="Card.TLabel", wraplength=420, justify="left")
        self.status_label.pack(anchor="w", fill="x", expand=True)

        buttons = ttk.Frame(body, style="TFrame")
        buttons.pack(fill="x", pady=(12, 0))
        self.check_button = ttk.Button(buttons, text="Check Now", command=self._check_now, style="Accent.TButton")
        self.check_button.pack(side="left")
        self.download_button = ttk.Button(buttons, text="Download Latest", command=self._download_latest, state="disabled")
        self.download_button.pack(side="left", padx=8)
        ttk.Button(buttons, text="Close", command=self._close).pack(side="right")

        self._show_cached_state()
        # Auto-check on open only if we have no cached result yet.
        parent_store = parent.update_store if isinstance(parent, IAInteractGUI) else None
        if ia_update is None or parent_store is None or not parent_store.get_last_release_tag():
            self.after(100, self._check_now)

        try:
            self.wait_visibility()
        except tk.TclError:
            pass
        try:
            self.grab_set()
        except tk.TclError:
            pass
        try:
            self.focus_set()
        except tk.TclError:
            pass

    def _show_cached_state(self):
        parent = self.parent
        if ia_update is None or not isinstance(parent, IAInteractGUI) or parent.update_store is None:
            self.status_label.configure(text="Update checker unavailable.")
            return
        tag = parent.update_store.get_last_release_tag()
        if not tag:
            self.status_label.configure(text="Not checked yet. Click \"Check Now\".")
            return
        self._latest_tag = tag
        cmp = ia_update.compare_versions(__version__, tag)
        if cmp < 0:
            self.status_label.configure(text=f"Update available: {tag}\n(installed v{__version__}).")
            self.download_button.configure(state="normal")
        elif cmp == 0:
            self.status_label.configure(text=f"You are up to date (latest {tag}).")
            self.download_button.configure(state="disabled")
        else:
            self.status_label.configure(text=f"Running a newer build than the latest release ({tag}).")
            self.download_button.configure(state="disabled")

    def _check_now(self):
        parent = self.parent
        if not isinstance(parent, IAInteractGUI):
            return
        self.check_button.configure(state="disabled")
        self.download_button.configure(state="disabled")
        self.status_label.configure(text="Checking for updates...")
        parent._run_update_check(manual=True, on_result=self._apply_result)

    def _apply_result(self, result):
        self.check_button.configure(state="normal")
        if not result.get("ok"):
            self.status_label.configure(text=f"Update check failed:\n{result.get('error') or 'unknown error'}")
            self.download_button.configure(state="disabled")
            return
        tag = result.get("tag") or ""
        self._latest_tag = tag or self._latest_tag
        if result.get("available"):
            self.status_label.configure(text=f"Update available: {tag}\n(installed v{__version__}).")
            self.download_button.configure(state="normal")
        elif result.get("cmp") == 0:
            self.status_label.configure(text=f"You are up to date (latest {tag}).")
            self.download_button.configure(state="disabled")
        else:
            self.status_label.configure(text=f"Running a newer build than the latest release ({tag}).")
            self.download_button.configure(state="disabled")

    def _download_latest(self):
        tag = self._latest_tag
        url = None
        if tag and ia_update is not None:
            url = ia_update.build_release_asset_url(tag)
        if not url:
            url = ia_update.RELEASES_LATEST_URL if ia_update is not None else None
        if not url:
            messagebox.showerror("Download", "No download URL available for this platform.")
            return
        try:
            webbrowser.open(url)
        except Exception as exc:
            messagebox.showerror("Download", f"Failed to open browser:\n{exc}")

    def _close(self):
        self.destroy()


def main():
    app = IAInteractGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
