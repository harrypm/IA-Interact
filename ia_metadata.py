"""Internet Archive item-metadata writer for IA Interact.

Implements the metadata WRITE API the way archive.org's own tooling does:

    POST https://archive.org/metadata/<identifier>
    form fields:  -patch  = JSON-Patch operations (draft-02 style)
                  -target = metadata
                  priority = -5
    header:       Authorization: LOW <access_key>:<secret_key>

NOTE: posting form data to ``/metadata/<identifier>/metadata`` (the old
assumption) does NOT hit the write API - it returns the website's HTML page
with HTTP 200 and silently writes NOTHING. That was issue #9.

Pure standard library + requests; importable by CLI and GUI, headless-testable,
bundled by PyInstaller.
"""

import json

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover
    requests = None

METADATA_WRITE_TIMEOUT = (30, 120)


def _json_pointer_escape(key):
    """Escape a metadata key for use in a JSON-Patch path (RFC 6901)."""
    return key.replace("~", "~0").replace("/", "~1")


def _patch_operations(metadata, existing_keys):
    """Build add/replace operations: replace for existing keys, add for new."""
    operations = []
    for key, value in metadata.items():
        if not key:
            continue
        op = "replace" if key in existing_keys else "add"
        operations.append(
            {"op": op, "path": "/" + _json_pointer_escape(key), "value": value}
        )
    return operations


def write_item_metadata(identifier, metadata, access_key, secret_key,
                        proxies=None, timeout=METADATA_WRITE_TIMEOUT):
    """Write item metadata for ``identifier``. Returns (ok, detail).

    Reads the item's current metadata to decide add-vs-replace per key
    (archive.org auto-sets ``title`` to the identifier on creation, so the
    title needs a replace, not an add).
    """
    if requests is None:
        return False, "requests unavailable"

    existing_keys = set()
    try:
        response = requests.get(
            "https://archive.org/metadata/" + identifier, timeout=timeout,
            proxies=proxies,
        )
        if response.status_code == 200:
            data = response.json()
            existing = data.get("metadata") if isinstance(data, dict) else None
            if isinstance(existing, dict):
                existing_keys = set(existing.keys())
    except (requests.RequestException, ValueError):
        pass  # fall through: everything becomes an "add"

    operations = _patch_operations(metadata, existing_keys)
    if not operations:
        return False, "no metadata to write"

    try:
        response = requests.post(
            "https://archive.org/metadata/" + identifier,
            data={
                "-patch": json.dumps(operations),
                "-target": "metadata",
                "priority": "-5",
            },
            headers={"Authorization": "LOW %s:%s" % (access_key, secret_key)},
            timeout=timeout,
            proxies=proxies,
        )
    except requests.RequestException as exc:
        return False, "Metadata write failed: %s" % exc

    if response.status_code != 200:
        return False, (
            "Metadata write failed: %s %s" % (response.status_code, response.reason)
        )
    # Success body is JSON like {"success": true, "task_id": ...}; the failure
    # mode of the old code (HTML page) is excluded by checking the content.
    try:
        payload = response.json()
    except ValueError:
        return False, "Metadata write returned a non-API response (HTML page)"
    if isinstance(payload, dict) and payload.get("success"):
        return True, "Metadata written (task %s)" % payload.get("task_id", "?")
    return False, "Metadata write rejected: %s" % response.text[:200]
