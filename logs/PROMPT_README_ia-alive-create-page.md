# Prompt Log — IA-alive status indicator, offline action queue, and Create Repository page

- Date: 2026-11-05
- Task: Add an "IA alive" status indicator; when the check reports IA down, queue upload/download/creation. Also (from the prior message) port the repo-creation page of https://github.com/Xitee1/ia-gui.
- Working directory: `C:\Users\Harry\IA-Interact` (branch: `main`)

## User input (original requests)

> I want to copy the repo creation page of https://github.com/Xitee1/ia-gui
> I want to add an "is AI alive" status indicator and if it calls back as not queue the upload/download/creation

(Note: "AI alive" interpreted as "IA (Internet Archive) alive" - reachability of archive.org.)

## Research (hard data)

- Fetched ia-gui source (PySide6 + internetarchive lib): `widgets/editor_panel.py`,
  `ia_client.py`, `constants.py`. Copied concepts: required section (title,
  identifier + Check + note, description, subjects, collection), optional
  (creator, date, language, license), custom metadata key/value rows,
  COLLECTIONS/LICENSES/LANGUAGES constants, identifier regex
  `^[a-zA-Z0-9][a-zA-Z0-9._-]{4,99}$`, slugify_identifier(title).
- Live endpoint verification (curl):
  - `GET https://archive.org/metadata/nasa` -> 200, ~0.8s (health probe).
  - `GET https://archive.org/metadata/<nonexistent>` -> **HTTP 200 with `{}`**,
    so identifier availability must check the parsed payload
    (`metadata.identifier`), not the status code.

## Changes made (all in `ia_interact_gui.py` + `README.md`)

1. Constants/helpers: `IA_COLLECTIONS`, `IA_LICENSES`, `IA_LANGUAGES`,
   `IA_IDENTIFIER_RE`, `slugify_identifier()`, `identifier_valid()`,
   `check_ia_alive()`, `identifier_exists()`, `IA_HEALTH_URL`.
2. Status indicator: `IA: Online` (green) / `IA: Offline` (red) /
   `IA: checking...` label in the main-screen account bar; click = re-check.
   Background checks on an adaptive schedule (20s offline/queued, 300s online);
   status transitions are logged.
3. Offline action queue: `_submit_ia_action(kind, description, starter)` runs
   the action immediately when IA is alive; when offline (or status unknown)
   it queues it and shows a "Queued - waiting for archive.org" panel
   (Try now / Remove selected). On a successful check the queue drains in FIFO
   order automatically.
4. Wiring: `upload_selected_local_files` and `download_selected_repository_files`
   refactored into `_start_upload` / `_start_download` worker-launchers routed
   through the queue.
5. `CreateRepositoryDialog` (ia-gui-style creation page, tkinter port):
   required title / identifier (+ Check availability, slug auto-suggest,
   live validation note) / description / subject tags / collection (editable,
   custom ids allowed); optional creator / date / language / license; custom
   key:value metadata rows. Creation uploads the Local Files list to the new
   identifier (x-amz-auto-make-bucket creates the item) then POSTs item
   metadata (same flow as the CLI create option), re-verifies identifier
   availability at run time, then reloads the repository list. The whole
   creation goes through the offline queue.
6. Shutdown resilience: health/probe worker threads tolerate the mainloop
   being gone (RuntimeError swallowed around after()).

## Verification

- `py_compile`: OK.
- Headless tests (real Tk, no mainloop; Python 3.12.10):
  - Helpers: slugify cases, identifier validation edge cases (length, leading
    char, charset) - OK.
  - Live API: check_ia_alive True; identifier_exists nasa=True, bogus=False - OK.
  - Indicator states (checking/online/offline) - OK.
  - Queue: offline -> queued + panel shown, not run; alive -> drain runs in
    order; alive -> immediate run; unknown -> queues + probes; remove-selected - OK.
  - Create dialog: slug auto-suggest, identifier note preview, metadata
    collection (collection label->id mapping incl. custom id, subjects,
    creator/date/language/licenseurl, custom rows), row add/remove - OK.
- First harness run exposed a test-only hang (`wait_visibility()` needs a
  mainloop; patched in the harness) plus the real shutdown-race finding fixed
  in item 6 above.

## Status

- Implemented and unit-verified; NOT committed/pushed (awaiting user go-ahead;
  note: issue-#6 fix from the earlier session is also still uncommitted).
- Real-GUI confirmation by the user still pending (per rule: user-interactable
  elements need real-world confirmation).

## Commands run

| # | Command | Result |
|---|---------|--------|
| 1 | fetch github.com/Xitee1/ia-gui + README | Feature overview |
| 2 | fetch API tree, raw ia_client.py/constants.py | Constants + API surface |
| 3 | curl raw editor_panel.py, read locally | Full form structure |
| 4 | curl metadata/nasa + bogus id | 200 / 200+{} semantics |
| 5 | edit_files x5 | Feature implementation |
| 6 | py_compile + 2 headless test runs | All passed after harness fix |
