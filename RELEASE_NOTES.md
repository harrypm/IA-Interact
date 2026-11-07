# IA Interact @TAG@

A native desktop client for uploading, downloading and managing Internet Archive repositories.

<!-- Screenshots are captured automatically by the release pipeline
     (tests/capture_screenshots.py on the Windows build job) and attached
     as release assets; the links below are validated against the built
     assets by the release workflow. -->

| Login | Main window |
---|---|
![Login](https://github.com/harrypm/IA-Interact/releases/download/@TAG@/gui-login.png) | ![Main window](https://github.com/harrypm/IA-Interact/releases/download/@TAG@/gui-main.png) |

![Create Repository page](https://github.com/harrypm/IA-Interact/releases/download/@TAG@/gui-create-repository.png)

## New in @TAG@

- **Window menu** (top bar) with user choices: **Remember window size/position** (opt-in; restores your last size/position on open), **Auto-expand folders when added**, and **Log uploads to .txt** (per-file upload record at `~/IA-Interact/uploads-log.txt`).
- **Create Repository metadata fix** — the previous version's metadata write silently hit the website HTML page and wrote nothing (items showed the identifier as the title). Metadata now writes through the real JSON-Patch API and is verified to land: title, description, subject tags, collection, license, and custom fields.
- **Empty folders** show in folder view as `(0 files)` rows (uploads nothing — archive.org stores files only).
- **Clickable +/−** tree markers (single-click expand/collapse; double-click the row still works).
- **Expand all / Collapse all** buttons on both file viewers.
- **Progress panel** no longer squeezes the neighbouring panels during transfers.

## Highlights

- **Create Repository page** — full item-creation form (title, identifier with availability check + slug auto-suggest, description, subject tags, collection, creator/date/language/license, custom key:value metadata), modeled on ia-gui's editor.
- **IA status indicator + offline queue** — live `IA: Online / Offline` badge; uploads, downloads and creations queue automatically while archive.org is unreachable and start when it's back.
- **Network profiles** — named proxy configs (direct / system / manual with optional auth) + connectivity test; all GUI archive.org traffic follows the active profile.
- **Folder view** — collapsible folder trees in both file viewers (folders start collapsed, `+`/`−` expand markers, folder-aware select/remove).
- **Retries selector (0 = unlimited)** — transfers retry transient failures with exponential backoff until they stick.
- **Skip existing** — identical files (name + repository path + size, md5-verified when available) are skipped, so re-running an interrupted upload resumes where it stopped.
- **Derivatives off by default** — items upload as-is (`x-archive-queue-derive: 0`); archive.org transcodes can be enabled per upload or triggered later from the item's page.
- **CLI parity** — the GUI gained delete/move file operations; credentials resolve from env vars or saved GUI profiles on both front-ends.
- **Clean launches** — windowed build (no empty console), GUI opens centered on the main monitor, dialogs open over the main window.

## Downloads

| Platform | Download |
|---|---|
| Windows x86_64 | [ia-interact-windows-x86_64.exe](https://github.com/harrypm/IA-Interact/releases/download/@TAG@/ia-interact-windows-x86_64.exe) |
| Windows ARM64 | [ia-interact-windows-arm64.exe](https://github.com/harrypm/IA-Interact/releases/download/@TAG@/ia-interact-windows-arm64.exe) |
| Linux x86_64 | [ia-interact-linux-x86_64.AppImage](https://github.com/harrypm/IA-Interact/releases/download/@TAG@/ia-interact-linux-x86_64.AppImage) |
| Linux ARM64 | [ia-interact-linux-aarch64.AppImage](https://github.com/harrypm/IA-Interact/releases/download/@TAG@/ia-interact-linux-aarch64.AppImage) |
| macOS (Apple Silicon + Intel) | [ia-interact-macos-universal.app.zip](https://github.com/harrypm/IA-Interact/releases/download/@TAG@/ia-interact-macos-universal.app.zip) |

## Notes

- S3 keys come from `S3_ACCESS_KEY` / `S3_SECRET_KEY` environment variables — or save them once in the GUI Account Manager (the CLI reuses the active profile).
- Get keys at [archive.org/account/s3.php](https://archive.org/account/s3.php). Keys never leave your machine except to authenticate with archive.org.
