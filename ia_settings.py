"""Local user-preference storage for IA Interact (Window menu options).

Stores small user choices - remember window size/position, auto-expand
folders when added, log uploads to .txt - in a JSON file next to the
accounts/network/drafts stores.

Pure standard library so it can be imported by the GUI, exercised headless,
and bundled by PyInstaller.
"""

import json
import os
import tempfile

CONFIG_DIRNAME = "ia-interact"
CONFIG_FILENAME = "settings.json"
SCHEMA_VERSION = 1

DEFAULTS = {
    "remember_window": False,       # issue #13: opt-in, never forced
    "window_geometry": "",          # last geometry string (when remember on)
    "auto_expand_folders": False,   # issue #12
    "log_uploads_txt": False,       # issue #8
}


def _default_config_dir():
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return os.path.join(base, CONFIG_DIRNAME)
    return os.path.join(os.path.expanduser("~"), ".config", CONFIG_DIRNAME)


class SettingsStore:
    """Flat key/value settings persisted atomically (0600)."""

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
        self._data = dict(DEFAULTS)
        if os.path.exists(self._config_path):
            try:
                with open(self._config_path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
            except (OSError, ValueError):
                raw = {}
            if isinstance(raw, dict):
                for key, default in DEFAULTS.items():
                    value = raw.get(key, default)
                    if isinstance(value, type(default)):
                        self._data[key] = value
        return self._data

    def save(self):
        self._ensure_loaded()
        self._ensure_dir()
        fd, tmp_path = tempfile.mkstemp(
            prefix=".settings.", suffix=".tmp", dir=self._config_dir
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

    def get(self, key):
        self._ensure_loaded()
        return self._data.get(key, DEFAULTS.get(key))

    def set(self, key, value):
        if key not in DEFAULTS:
            raise ValueError("Unknown setting: %s" % key)
        if not isinstance(value, type(DEFAULTS[key])):
            raise ValueError("Wrong type for %s" % key)
        self._ensure_loaded()
        self._data[key] = value
        self.save()
