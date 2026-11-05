# IA-Interact — CLI / GUI Feature Parity

Audit date: 2026-11-05. Goal: both front-ends expose the same core abilities;
front-end-specific conveniences are listed per side.

## Core operations (should mirror)

| Feature | CLI | GUI | Notes |
|---|---|---|---|
| Upload files to existing item | ✅ | ✅ | files + folders, structure preserved |
| Upload via drag & drop | ❌ | ✅ | GUI-only interaction |
| Download file / folder / all | ✅ | ✅ | directory-style listing both sides |
| List/view repository files | ✅ flat | ✅ tree+flat | GUI adds collapsible folder view |
| Delete file(s) from item | ✅ | ✅ | GUI: right-click → Delete Selected |
| Move file within item | ✅ | ✅ | GUI: right-click → Move Selected |
| Create repository (item + metadata) | ✅ | ✅ | GUI: full form (ia-gui style) |
| Retry failed transfers | ✅ fixed 5× (urllib3) | ✅ selector 0=∞ | 0 = unlimited on both... CLI exposes no setting yet (backlog candidate) |
| Skip already-uploaded files | ❌ | ✅ Skip existing | GUI: name+path+size+md5; CLI: backlog candidate |
| Derive (x-archive-queue-derive) toggle | ⚠️ rules file only | ✅ per-upload + create | CLI create writes `CAT.ALL` rules file; CLI uploads have no toggle (backlog) |
| Test item mode | ✅ (test_item prompt) | ✅ (test collection) | items auto-delete after 30 days |

## GUI-only conveniences

- Account profiles (S3 key manager); CLI consumes the active profile as a fallback.
- Network profiles (proxy: direct/system/manual) + connectivity test.
- IA status indicator + offline action queue (uploads/downloads/creation auto-queue).
- Drafts (save/apply upload workspaces).
- My Repositories browser (screenname-based), update checker, dark theme, DPI scaling.

## CLI-only conveniences

- Scriptable flags (`--gui` / `--cli` / `--version`), runs anywhere Python runs.
- (Backlog) expose retries/skip-existing/derive as CLI flags for full parity.
