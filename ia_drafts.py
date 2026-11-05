"""Local draft storage for IA Interact.

A draft is a named, saved upload workspace: the repository field, target
upload directory, keep-folder-name flag, and the queued local file list.
Concept modeled on ia-gui's drafts (a work-in-progress upload including the
selected files), reimplemented for IA-Interact's tkinter GUI and JSON config
conventions.

Stored in ~/.config/ia-interact/drafts.json (or $XDG_CONFIG_HOME/...),
written atomically with 0600 permissions (directory 0700).

This module is pure standard library so it can be imported by the GUI,
exercised headless, and bundled by PyInstaller.
"""

import json
import os
import tempfile
import time

CONFIG_DIRNAME = "ia-interact"
CONFIG_FILENAME = "drafts.json"
SCHEMA_VERSION = 1


def _default_config_dir():
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return os.path.join(base, CONFIG_DIRNAME)
    return os.path.join(os.path.expanduser("~"), ".config", CONFIG_DIRNAME)


class DraftStore:
    """Manages named drafts (upsert-by-name) in a small JSON file."""

    def __init__(self, config_dir=None):
        self._config_dir = config_dir or _default_config_dir()
        self._config_path = os.path.join(self._config_dir, CONFIG_FILENAME)
        self._data = None

    def _ensure_dir(self):
        os.makedirs(self._config_dir, exist_ok=True)
        try:
            os.chmod(self._config_dir, 0o700)
        except OSError:
            pass

    def _ensure_loaded(self):
        if self._data is None:
            self.load()

    def load(self):
        """Load drafts from disk, returning a normalized data dict."""
        self._data = {"version": SCHEMA_VERSION, "drafts": []}
        if os.path.exists(self._config_path):
            try:
                with open(self._config_path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
            except (OSError, ValueError):
                raw = {}
            if isinstance(raw, dict):
                drafts = raw.get("drafts")
                if isinstance(drafts, list):
                    cleaned = []
                    for draft in drafts:
                        if not isinstance(draft, dict) or not draft.get("name"):
                            continue
                        cleaned.append(
                            {
                                "name": str(draft["name"]),
                                "created": draft.get("created", time.time()),
                                "updated": draft.get("updated", time.time()),
                                "data": draft.get("data", {}) if isinstance(draft.get("data"), dict) else {},
                            }
                        )
                    self._data["drafts"] = cleaned
        return self._data

    def save(self):
        """Persist drafts to disk atomically with 0600 permissions."""
        self._ensure_loaded()
        self._ensure_dir()
        fd, tmp_path = tempfile.mkstemp(
            prefix=".drafts.", suffix=".tmp", dir=self._config_dir
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._data, handle, indent=2)
                handle.write("\n")
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
            os.replace(tmp_path, self._config_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _find_index(self, name):
        for index, draft in enumerate(self._data["drafts"]):
            if draft.get("name") == name:
                return index
        return -1

    def list_drafts(self):
        """All drafts, newest-updated first. Each: {name, created, updated, data}."""
        self._ensure_loaded()
        drafts = [dict(d) for d in self._data["drafts"]]
        drafts.sort(key=lambda d: d.get("updated", 0), reverse=True)
        return drafts

    def get_draft(self, name):
        self._ensure_loaded()
        index = self._find_index(name)
        if index < 0:
            return None
        return dict(self._data["drafts"][index])

    def save_draft(self, name, data):
        """Create or overwrite (upsert by name) a draft with a data dict."""
        self._ensure_loaded()
        name = (name or "").strip()
        if not name:
            raise ValueError("Draft name is required.")
        if not isinstance(data, dict):
            raise ValueError("Draft data must be a dict.")
        now = time.time()
        index = self._find_index(name)
        if index >= 0:
            draft = self._data["drafts"][index]
            draft["data"] = data
            draft["updated"] = now
        else:
            self._data["drafts"].append(
                {"name": name, "created": now, "updated": now, "data": data}
            )
        self.save()

    def delete_draft(self, name):
        self._ensure_loaded()
        index = self._find_index(name)
        if index < 0:
            raise ValueError("No draft named '%s'." % name)
        del self._data["drafts"][index]
        self.save()
