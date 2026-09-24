"""Release update checker for IA Interact.

Mirrors the MISRC-GUI update checker (misrc_tools/misrc_gui/ui/gui_ui.c):
- Discovers the latest release tag by following the GitHub
  ``/<owner>/<repo>/releases/latest`` redirect and parsing the ``/releases/tag/<tag>``
  segment of the final URL. This is redirect-based (no GitHub API call), so it
  needs no token and does not hit the unauthenticated-API rate limit.
- Compares the current and latest version as a semver major.minor.patch triplet,
  with a plain string fallback for non-semver tags.
- Builds the platform-appropriate release asset download URL for this build.
- Persists the last check time, last seen release tag, and cached
  "update available" flag to ``~/.config/ia-interact/update.json`` (atomic write,
  0600 file / 0700 dir, corrupt-file tolerant) so an automatic background
  check runs at most once every ``CHECK_INTERVAL_SECONDS`` (7 days).

Pure standard library + ``requests`` only (no tkinter), so it can be imported
by the GUI, exercised headless, and bundled by PyInstaller.
"""

import json
import os
import platform
import re
import tempfile
import time

try:
    import requests
except ModuleNotFoundError:
    requests = None  # The GUI imports lazily; checker is a no-op if missing.

# --- Repository / release constants --------------------------------------

GITHUB_OWNER = "harrypm"
GITHUB_REPO = "IA-Interact"
RELEASES_LATEST_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
RELEASES_DOWNLOAD_BASE_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/download"

# Mirror MISRC-GUI: automatic background check runs at most once every 7 days.
CHECK_INTERVAL_SECONDS = 7 * 24 * 60 * 60

# Network timeout for the redirect lookup (connect, read) in seconds.
FETCH_TIMEOUT = (10, 15)

_CONFIG_DIRNAME = "ia-interact"
_UPDATE_FILENAME = "update.json"


def _default_config_dir():
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return os.path.join(base, _CONFIG_DIRNAME)
    return os.path.join(os.path.expanduser("~"), ".config", _CONFIG_DIRNAME)


def extract_release_tag_from_url(url):
    """Return the release tag from a ``/releases/tag/<tag>`` URL, else None.

    Strips any trailing query/fragment/path so ``/releases/tag/v1.2.3`` and
    ``/releases/tag/v1.2.3?foo=bar`` both yield ``v1.2.3``.
    """
    if not url:
        return None
    marker = "/releases/tag/"
    idx = url.find(marker)
    if idx < 0:
        return None
    start = idx + len(marker)
    if start >= len(url):
        return None
    end = start
    while end < len(url) and url[end] not in "/?#":
        end += 1
    tag = url[start:end]
    return tag or None


def parse_semver(version):
    """Parse a ``vX.Y.Z`` / ``X.Y.Z`` string into (major, minor, patch) ints.

    Returns None if the string is not a clean semver triplet (no pre-release).
    Matches MISRC-GUI's ``gui_ui_parse_semver_triplet``.
    """
    if version is None:
        return None
    text = version.strip()
    if text.startswith(("v", "V")):
        text = text[1:]
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def compare_versions(current_version, latest_version):
    """Compare two version strings.

    Returns -1 if current < latest, 0 if equal, 1 if current > latest. Falls
    back to a plain string compare when either side is not a clean semver
    triplet (matching MISRC-GUI's ``gui_ui_compare_versions``).
    """
    current_parsed = parse_semver(current_version)
    latest_parsed = parse_semver(latest_version)
    if current_parsed and latest_parsed:
        for cur, lat in zip(current_parsed, latest_parsed):
            if cur != lat:
                return -1 if cur < lat else 1
        return 0
    cur_text = current_version or ""
    lat_text = latest_version or ""
    if cur_text < lat_text:
        return -1
    if cur_text > lat_text:
        return 1
    return 0


def is_release_tag_safe(tag):
    """True if ``tag`` only contains chars safe to interpolate into a URL."""
    if not tag:
        return False
    return bool(re.match(r"^[A-Za-z0-9._-]+$", tag))


def build_release_asset_filename(release_tag):
    """Return this platform's release asset filename for ``release_tag``.

    The names match the artifact -> release-asset names produced by
    .github/workflows/build-binaries.yml. Returns None if there is no mapping
    for the current platform.
    """
    if not is_release_tag_safe(release_tag):
        return None

    system = platform.system()
    machine = platform.machine().lower()

    if system == "Linux":
        arch = "aarch64" if machine in ("aarch64", "arm64") else "x86_64"
        return f"ia-interact-linux-{arch}.AppImage"
    if system == "Windows":
        arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
        return f"ia-interact-windows-{arch}.exe"
    if system == "Darwin":
        return "ia-interact-macos-universal.app.zip"
    return None


def build_release_asset_url(release_tag):
    """Return the full download URL for this platform's asset, else None."""
    filename = build_release_asset_filename(release_tag)
    if not filename:
        return None
    return f"{RELEASES_DOWNLOAD_BASE_URL}/{release_tag}/{filename}"


def fetch_latest_release_tag():
    """Follow the releases/latest redirect and return (tag, None) or (None, error).

    Uses ``requests`` when available (the GUI already depends on it). Falls back
    to stdlib ``urllib.request`` so the CLI's update check still works in a
    requests-less environment. The error string is human-readable.
    """
    if requests is not None:
        try:
            response = requests.get(RELEASES_LATEST_URL, allow_redirects=True, timeout=FETCH_TIMEOUT)
        except requests.RequestException as exc:
            return None, f"network request failed: {exc}"
        final_url = response.url
        if not final_url:
            return None, "latest release redirect URL was empty"
        tag = extract_release_tag_from_url(final_url)
        if not tag:
            return None, "could not parse release tag from redirect URL"
        return tag, None

    # Stdlib fallback (no requests dependency).
    import urllib.request
    import urllib.error

    req = urllib.request.Request(RELEASES_LATEST_URL, headers={"User-Agent": "ia-interact-update-check"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT[1]) as handle:
            final_url = handle.geturl()
    except urllib.error.URLError as exc:
        return None, f"network request failed: {exc.reason}"
    except Exception as exc:  # noqa: BLE001 — surface any unexpected failure
        return None, f"network request failed: {exc}"
    if not final_url:
        return None, "latest release redirect URL was empty"
    tag = extract_release_tag_from_url(final_url)
    if not tag:
        return None, "could not parse release tag from redirect URL"
    return tag, None


class UpdateStore:
    """Persists update-check state to a small JSON file under the XDG config dir.

    Schema: {"last_check_unix_s": <float>, "last_release_tag": <str>,
             "update_available": <bool>}. Corrupt/missing files are tolerated
    (reset to defaults), matching ia_accounts.py's resilience approach.
    """

    def __init__(self, config_dir=None):
        self._config_dir = config_dir or _default_config_dir()
        self._config_path = os.path.join(self._config_dir, _UPDATE_FILENAME)
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
        """Load state from disk, returning a normalized data dict."""
        self._data = {
            "last_check_unix_s": 0.0,
            "last_release_tag": "",
            "update_available": False,
        }
        if os.path.exists(self._config_path):
            try:
                with open(self._config_path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
            except (OSError, ValueError):
                raw = {}
            if isinstance(raw, dict):
                last_check = raw.get("last_check_unix_s")
                if isinstance(last_check, (int, float)) and last_check >= 0:
                    self._data["last_check_unix_s"] = float(last_check)
                tag = raw.get("last_release_tag")
                if isinstance(tag, str):
                    self._data["last_release_tag"] = tag
                available = raw.get("update_available")
                if isinstance(available, bool):
                    self._data["update_available"] = available
        return self._data

    def save(self):
        """Persist state to disk atomically with 0600 permissions."""
        self._ensure_loaded()
        self._ensure_dir()
        fd, tmp_path = tempfile.mkstemp(prefix=".update.", suffix=".tmp", dir=self._config_dir)
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

    def is_due(self, now=None):
        """True if an automatic check is due (never checked, or 7 days elapsed)."""
        self._ensure_loaded()
        now = time.time() if now is None else now
        last = self._data.get("last_check_unix_s", 0.0) or 0.0
        if last <= 0:
            return True
        if now < last:
            return True
        return (now - last) >= CHECK_INTERVAL_SECONDS

    def get_last_release_tag(self):
        self._ensure_loaded()
        return self._data.get("last_release_tag", "") or ""

    def get_update_available(self):
        self._ensure_loaded()
        return bool(self._data.get("update_available", False))

    def record(self, latest_tag, current_version, now=None):
        """Record a completed check: stamp the time, cache the tag + availability."""
        self._ensure_loaded()
        now = time.time() if now is None else now
        self._data["last_check_unix_s"] = float(now)
        self._data["last_release_tag"] = latest_tag or ""
        self._data["update_available"] = bool(compare_versions(current_version, latest_tag) < 0)
        self.save()
        return self._data["update_available"]


def check_for_update(current_version):
    """Run a synchronous check and return a small result dict.

    Keys: ``ok`` (bool), ``tag`` (str, latest tag on success), ``available``
    (bool), ``cmp`` (-1/0/1), ``error`` (str on failure). Does NOT touch the
    UpdateStore — callers decide whether to persist.
    """
    tag, error = fetch_latest_release_tag()
    if not tag:
        return {
            "ok": False,
            "tag": "",
            "available": False,
            "cmp": None,
            "error": error or "unknown error",
        }
    cmp = compare_versions(current_version, tag)
    return {
        "ok": True,
        "tag": tag,
        "available": cmp < 0,
        "cmp": cmp,
        "error": None,
    }
