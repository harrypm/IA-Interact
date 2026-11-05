"""Local network profile storage for IA Interact.

Stores named network profiles (proxy configuration) in a JSON file next to
the account profiles so the GUI can switch network setups without editing
environment variables or system settings.

Modes:
  direct - never use a proxy (explicitly overrides any env vars)
  system - follow the environment (HTTP_PROXY / HTTPS_PROXY, system settings)
  manual - use the stored proxy (host/port, optional user/password)

This module is pure standard library (no tkinter, no requests) so it can be
imported by the GUI, exercised headless, and bundled by PyInstaller.

Passwords are stored in plaintext alongside the other profile data; the file
is written with 0600 permissions (directory 0700) like accounts.json. Do not
store credentials on shared machines.
"""

import json
import os
import tempfile

CONFIG_DIRNAME = "ia-interact"
CONFIG_FILENAME = "network.json"
SCHEMA_VERSION = 1

PROXY_MODES = ("direct", "system", "manual")


def _default_config_dir():
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return os.path.join(base, CONFIG_DIRNAME)
    return os.path.join(os.path.expanduser("~"), ".config", CONFIG_DIRNAME)


def proxy_url_from(profile):
    """Build a http://[user:pass@]host:port URL from a manual-mode profile."""
    if not profile or profile.get("mode") != "manual":
        return ""
    host = (profile.get("host") or "").strip()
    port = str(profile.get("port") or "").strip()
    if not host:
        return ""
    user = (profile.get("username") or "").strip()
    password = (profile.get("password") or "").strip()
    host_part = f"{host}:{port}" if port else host
    if user:
        return f"http://{user}:{password}@{host_part}"
    return f"http://{host_part}"


class NetworkStore:
    """Manages a small JSON-backed collection of named network profiles."""

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
                                "mode": profile.get("mode", "system"),
                                "host": profile.get("host", ""),
                                "port": profile.get("port", ""),
                                "username": profile.get("username", ""),
                                "password": profile.get("password", ""),
                            }
                        )
                    self._data["profiles"] = cleaned
        return self._data

    def save(self):
        """Persist profiles to disk atomically with 0600 permissions."""
        self._ensure_loaded()
        self._ensure_dir()
        fd, tmp_path = tempfile.mkstemp(
            prefix=".network.", suffix=".tmp", dir=self._config_dir
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

    def add_profile(self, name, mode, host="", port="", username="", password=""):
        self._ensure_loaded()
        name = (name or "").strip()
        mode = (mode or "system").strip()
        if not name:
            raise ValueError("Profile name is required.")
        if mode not in PROXY_MODES:
            raise ValueError("Mode must be one of: %s." % ", ".join(PROXY_MODES))
        if self._find_index(name) >= 0:
            raise ValueError("A profile named '%s' already exists." % name)
        self._data["profiles"].append(
            {
                "name": name,
                "mode": mode,
                "host": (host or "").strip(),
                "port": str(port or "").strip(),
                "username": (username or "").strip(),
                "password": password or "",
            }
        )
        self.save()

    def update_profile(self, name, mode=None, host=None, port=None,
                       username=None, password=None, rename=None):
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
        if mode is not None and mode.strip():
            mode = mode.strip()
            if mode not in PROXY_MODES:
                raise ValueError("Mode must be one of: %s." % ", ".join(PROXY_MODES))
            profile["mode"] = mode
        if host is not None:
            profile["host"] = host.strip()
        if port is not None:
            profile["port"] = str(port).strip()
        if username is not None:
            profile["username"] = username.strip()
        if password is not None:
            profile["password"] = password
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

    def get_active_profile(self):
        """The active profile dict, or None (treated as 'system')."""
        active = self.get_active()
        if not active:
            return None
        return self.get_profile(active)
