"""Single source of truth for the IA-Interact version string.

This module is read at runtime by the CLI (ia-interact.py --version) and the
GUI (window title / About dialog / update checker) and is rewritten by the
.github/workflows/release.yml "cut-release" job when cutting a release tag.

The value here carries NO leading 'v'; git release tags use the 'v' prefix
(e.g. __version__ == "1.0.0" <-> git tag v1.0.0).
"""

__version__ = "1.0.0"
