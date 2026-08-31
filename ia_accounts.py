"""Local S3 account profile storage for IA Interact.

Stores named Internet Archive S3 account profiles (access + secret keys) in a
JSON file under the XDG config directory so the GUI can remember, switch, edit,
and remove accounts without re-entering keys each session.

The config file is written with 0600 permissions and the containing directory
with 0700. Keys are stored in plaintext; rely on home-directory / full-disk
encryption for protection at rest. Do not store keys on shared machines.

This module is pure standard library (no tkinter, no requests) so it can be
imported by the GUI, exercised headless, and bundled by PyInstaller.
"""

import json
import os
import tempfile
import time

CONFIG_DIRNAME = "ia-interact"
CONFIG_FILENAME = "accounts.json"
SCHEMA_VERSION = 1


def _default_config_dir():
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return os.path.join(base, CONFIG_DIRNAME)
    return os.path.join(os.path.expanduser("~"), ".config", CONFIG_DIRNAME)


class AccountStore:
    """Manages a small JSON-backed collection of named S3 account profiles."""

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
        """Load profiles from disk, returning a normalized data dict."""
        self._data = {
            "version": SCHEMA_VERSION,
            "active_profile": None,
            "profiles": [],
        }
        if os.path.exists(self._config_path):
            try:
                with open(self._config_path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
            except (OSError, ValueError):
                raw = {}
            if isinstance(raw, dict):
                active = raw.get("active_profile")
                if isinstance(active, str) or active is None:
                    self._data["active_profile"] = active
                profiles = raw.get("profiles")
                if isinstance(profiles, list):
                    cleaned = []
                    for profile in profiles:
                        if not isinstance(profile, dict) or not profile.get("name"):
                            continue
                        cleaned.append(
                            {
                                "name": profile["name"],
                                "access_key": profile.get("access_key", ""),
                                "secret_key": profile.get("secret_key", ""),
                                "notes": profile.get("notes", ""),
                                "created": profile.get("created", time.time()),
                            }
                        )
                    self._data["profiles"] = cleaned
        return self._data

    def save(self):
        """Persist profiles to disk atomically with 0600 permissions."""
        self._ensure_loaded()
        self._ensure_dir()
        fd, tmp_path = tempfile.mkstemp(
            prefix=".accounts.", suffix=".tmp", dir=self._config_dir
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
        for index, profile in enumerate(self._data["profiles"]):
            if profile.get("name") == name:
                return index
        return -1

    def list_names(self):
        self._ensure_loaded()
        return [p["name"] for p in self._data["profiles"] if p.get("name")]

    def get_profile(self, name):
        self._ensure_loaded()
        for profile in self._data["profiles"]:
            if profile.get("name") == name:
                return dict(profile)
        return None

    def add_profile(self, name, access_key, secret_key, notes=""):
        self._ensure_loaded()
        name = (name or "").strip()
        access_key = (access_key or "").strip()
        secret_key = (secret_key or "").strip()
        if not name:
            raise ValueError("Profile name is required.")
        if not access_key or not secret_key:
            raise ValueError("Both access key and secret key are required.")
        if self._find_index(name) >= 0:
            raise ValueError("A profile named '%s' already exists." % name)
        self._data["profiles"].append(
            {
                "name": name,
                "access_key": access_key,
                "secret_key": secret_key,
                "notes": (notes or "").strip(),
                "created": time.time(),
            }
        )
        self.save()

    def update_profile(self, name, access_key=None, secret_key=None, notes=None, rename=None):
        self._ensure_loaded()
        index = self._find_index(name)
        if index < 0:
            raise ValueError("No profile named '%s' to update." % name)
        profile = self._data["profiles"][index]
        new_name = (rename or "").strip() or name
        if new_name != name and self._find_index(new_name) >= 0:
            raise ValueError("A profile named '%s' already exists." % new_name)
        if new_name != name:
            profile["name"] = new_name
        if access_key is not None and access_key.strip():
            profile["access_key"] = access_key.strip()
        if secret_key is not None and secret_key.strip():
            profile["secret_key"] = secret_key.strip()
        if notes is not None:
            profile["notes"] = notes.strip()
        if self._data.get("active_profile") == name and new_name != name:
            self._data["active_profile"] = new_name
        self.save()

    def remove_profile(self, name):
        self._ensure_loaded()
        index = self._find_index(name)
        if index < 0:
            raise ValueError("No profile named '%s' to remove." % name)
        del self._data["profiles"][index]
        if self._data.get("active_profile") == name:
            self._data["active_profile"] = None
        self.save()

    def set_active(self, name):
        self._ensure_loaded()
        if not name:
            self._data["active_profile"] = None
        else:
            if self._find_index(name) < 0:
                raise ValueError("No profile named '%s'." % name)
            self._data["active_profile"] = name
        self.save()

    def get_active(self):
        self._ensure_loaded()
        active = self._data.get("active_profile")
        if active and self._find_index(active) >= 0:
            return active
        return None
