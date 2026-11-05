# Prompt Log — Network page (proxy profiles), retries selector, wide layout

- Date: 2026-11-05
- Task: Wide GUI layout rework; Network tab with proxy config/profile support; retries amount selector on the Progress panel; Android version added to backlog.
- Working directory: `C:\Users\Harry\IA-Interact` (branch: `main`)

## User input (original requests)

> good commit, lets now fix the gui layout I want it more like the layout that outher tool has more wide less vertical, but a vertical mode would make sence for mobile devices, lets add an android version to the list and we can work on that a bit later
> also add network tab, add proxy network config/profile support and add an retrys amount selecter on the progress nox

## Changes made

1. Committed + pushed verified prior work as `1976d58`; restore point zip at
   `C:\Users\Harry\IA-Interact-restore-points\2026-11-05_create-page-ia-status-windowed-build.zip`.
2. Wide layout (`ia_interact_gui.py`):
   - Command strip "Repository & Actions" (one row): repo entry + Load Files +
     Target dir + Upload + Download.
   - File lists (Repository Files | Local Files) are the tall centerpiece.
   - Bottom info strip: My Repositories | Progress | Status side by side.
   - Window 1280x700 (was 1040x740); minsize 1060x560; status rows 4.
3. Network support:
   - New `ia_network.py` (stdlib-only, mirrors ia_accounts.py): NetworkStore
     (JSON at ~/.config/ia-interact/network.json, 0600/0700) + proxy_url_from().
   - GUI `_proxies()` maps active profile mode: direct -> {"http": None,
     "https": None} (overrides env), system -> None (env vars), manual ->
     http://[user:pass@]host:port.
   - Proxies threaded through: check_ia_alive, identifier_exists,
     scrape_search_items, fetch_account_repositories, fetch_repository_files,
     upload/download single-file, create-repository metadata POST.
   - NetworkDialog (Network button in account bar): profile dropdown,
     Save as new / Update / Remove / Use, mode combo, host/port/user/password
     (masked), Test connection (live probe via configured route), applies
     immediately (status log + indicator re-check).
4. Retries selector: Progress panel gets a Retries spinbox (0-10, default 5).
   GUI upload/download now have a real retry loop (transient errors + HTTP
   500/502/503/504, exponential backoff capped at 30s, per-attempt progress
   counter reset). The GUI previously had NO retries at all; the CLI keeps
   its urllib3 Retry (total=5).

## Verification

- py_compile OK (ia_interact_gui.py + ia_network.py).
- Headless tests: NetworkStore CRUD + persistence round-trip + URL building;
  _proxies() for all three modes; _retry_attempts() bounds; NetworkDialog
  form collect/load round-trip; layout smoke test (all widgets present,
  single instances, DnD enabled, queue panel pack/unpack) — ALL PASSED.
- Rebuilt windowed exe and relaunched for user visual verification.

## Follow-up user feedback (same session)

> There is no network tab next to the help tab... I did not want an in-window box
> also add a drafts and save drafts system - git clone and copy work form https://github.com/Xitee1/ia-gui as needed

- Menubar now reads: Network | Drafts | Help (in-window Network button removed).
  - Network menu: Network Settings... / Test Connection + radiobutton
    quick-switch for each saved profile (list rebuilds on changes).
  - Drafts menu: Save Draft... / Manage Drafts... + quick-apply entries.
- New `ia_drafts.py` (stdlib JSON store, ~/.config/ia-interact/drafts.json,
  0600, upsert-by-name).
- Draft = repo field + target dir + keep-folder flag + queued local file
  list; apply restores all of it and reports files missing on disk.
- New DraftsDialog: list (name / file count / updated), Apply, Delete.
- ia-gui cloned to C:\Users\Harry\ia-gui-reference as reference; its drafts
  concept (name + meta + files) was reimplemented for tkinter/JSON rather
  than copying AGPL PySide6 code (IA-Interact is GPLv3).
- Tests: DraftStore CRUD/persistence, menu structure, save/apply round-trip,
  DraftsDialog apply - ALL PASSED.

## Follow-up 2 (same session): window management

> also fix the window open system, it should open center of main monitor and sub windows should open otop main window centered
> Fix create repo scaling, also the Centering not fixed and it reset positon when I logged in.. and windows open positon did not follow ware I placed the main gui

- `_center_on_screen` rewritten: reads the explicitly-set geometry string
  (winfo_width is unreliable pre-map), max()ed with winfo/req sizes. Called
  ONCE at open only - the re-center on login was REMOVED (it reset the
  user's placement when the main screen built).
- `center_dialog` rewritten: same size resolution; primary-screen clamp
  removed so dialogs follow the main window onto any monitor.
- CreateRepositoryDialog scaling: fixed 680x660 geometry removed; the dialog
  now sizes to its DPI-scaled content (minsize kept as a floor).
- Real-mainloop verification (measured, not assumed): login 1280x700 at
  +1080+370 = exact center of 3440x1440; after simulated login at +200+150
  position kept (+200+150); NetworkDialog opened at +1068+625 = dead-center
  over a main window parked at +700+400. ALL PASSED.

## Follow-up 3 (same session): folder view + unlimited retries

> add a collpase folders view mode to the viewers
> also set 0 to do unlimited retrys for upload/download

- Both viewers get a Folder view toggle (on by default): collapsible folder
  tree, double-click a folder to collapse/expand, selecting a folder selects
  its files. Local viewer gains the tree (grouped by repository-side path);
  remote viewer gains a flat mode (synthetic rows keep context menu/selection
  working). Tree-aware Remove Selected (a folder row removes its contents).
- Retries selector: 0 = UNLIMITED (loops until success or Cancel; cancel now
  also cuts the backoff sleep). 1-10 = that many retries.
- User clarified: folders start COLLAPSED (only folder rows visible until a
  folder is expanded). Tracked via _local_expanded/_remote_expanded sets;
  +/- prefix on folder rows; fresh remote listing resets to collapsed.
- Tests: collapsed default + nested expand + tree-aware remove + flat modes
  for both viewers - ALL PASSED.
- User saw scaling glitch at window bottom during test runs -> the loadtest
  was flashing a half-laid-out window for a second; the harness now keeps
  the window withdrawn for the whole run (no flash, logic unaffected).
  Re-verified: 4/4 PASS hidden.

## Follow-up 4 (same session): issue coverage audit

> look though the rest of the issues on my repo and see if these fixes are covering all the issues

- #6 env vars: COVERED (resolver + docs + GUI-profile fallback).
- #4 auto-retry: COVERED (Retries selector, 0=unlimited; CLI has its own
  urllib3 Retry total=5).
- #3 GUI folder upload + structure: COVERED (DnD/Add Folder, structure,
  folder view).
- #7 disable Queue derivation: was create-page-only -> now also a Derive
  checkbox on the command bar (default OFF) governing regular uploads.
- #5 skip already-uploaded files: was missing -> now implemented: Skip
  existing checkbox (default ON) on the command bar; uploads and creation
  resume compare name + full repository path + size, md5-verified when the
  remote file carries a hash; path-sensitive so same files in different
  folders still upload; per-skip logging + summary. fetch_item_file_info(),
  file_md5(), is_duplicate_upload() added; _start_upload/_start_create
  extended (also resumes a partially-failed creation).
- Tests: duplicate semantics, live metadata parse, skip-existing worker
  (real mainloop) - ALL PASSED.

## Follow-up 5 (same session): parity audit + GUI load test

> Does CLI/GUI mirror eachouter in features and or can you add automated GUI via CLI loadtesting
> there is a test item mode btw

- Parity audit written to dev-notes/FEATURE-PARITY.md. GUI lacked delete/move
  -> ported: right-click Delete Selected / Move Selected on the Repository
  Files viewer (confirm prompts, retry loops, proxies, routed through the IA
  queue, list auto-reloads). CLI-parity flags moved to BACKLOG.
- New tests/gui_loadtest.py: drives the REAL GUI with HTTP-layer
  instrumentation (per-path simulated 503s at --fail-rate, fake download
  bodies, stateful mock health probe) so the genuine retry/backoff,
  skip-existing, derive flag, offline queue and progress code all execute;
  metrics report + PASS/FAIL checks; --live mode caps at test_collection.
- Load test verified: 30 files/15% flake/skip-existing/offline-start = 4/4
  PASS; unlimited-retry run survived a 90% flake rate (32 failures, 12/12
  uploaded, 44 attempts).
- test-item mode: already present (test collection in create page + live
  test mode) - user confirmed.

## Commit + release (user go-ahead)

> commit push, trigger a new release

- Batch committed as `efb38d1` (8 files, +2197/-144) and pushed.
- Restore point: C:\Users\Harry\IA-Interact-restore-points\
  2026-11-05_wide-layout-network-drafts-folder-view.zip
- Release: gh workflow run release.yml -f bump=minor -> run 41952304942
  (input is named `bump`, not version_bump - first dispatch 422'd).
  Tag cut: v1.1.0 (from v1.0.2); builds: linux-x86_64 + macos done,
  windows-x86_64/arm64 + linux-aarch64 building; release auto-published
  with assets on completion.

## Follow-up 6: release names, notes + screenshots, in-app update notes page

> Change GH release names to "AI Interact" add proper release notes and screen shots to future releases and update the current release page, also add an update call page to pull notes in when an new update is ready for the app.

- release.yml publish job: release name = "IA Interact vX.Y.Z"; body comes
  from RELEASE_NOTES.md at the tag (@TAG@ sed-swapped so screenshots pin to
  the release's repo tree); generated changelog still appended.
- RELEASE_NOTES.md template created (v1.1.0 highlights + screenshot +
  downloads table + key info).
- v1.1.0 release page updated NOW: renamed "IA Interact v1.1.0", curated
  notes + screenshot embed + preserved auto-generated changelog.
- ia_update.fetch_release_notes(tag=None, proxies=None): GitHub API notes
  fetcher; UpdateDialog grew a "What's new in <tag>" notes page - notes are
  pulled in automatically whenever an update is detected (manual or cached
  auto-check), read-only pane, error fallback. Headless-tested OK.
- Note: user typed "AI Interact"; used the app's actual name "IA Interact".
- Committed 9b53ca7 (rebased over the release bot's version bump aefaf07)
  and pushed - remote is v1.1.0 with __version__ = "1.1.0".

## Follow-up 7: reset and replace v1.1.0

> reset and replace v1.1.0 with these changes

- version.py reset to 1.0.2 (commit f789c92) so the cutter could re-bump.
- Old release + tag deleted (gh release delete --cleanup-tag).
- Re-cut via `gh workflow run release.yml -f release_tag=v1.1.0` (run
  41954324922, success) - tag now sits at HEAD including the release-notes
  system + in-app update notes page.
- Published: name "IA Interact v1.1.0", curated notes + screenshot embed
  + generated changelog, all 5 assets rebuilt.
- The in-app update-notes page IS in this release's exe now.

## Status

- v1.1.0 fully replaced; everything committed and pushed.

## Commands run

| # | Command | Result |
|---|---------|--------|
| 1 | git commit 1976d58 + push + restore zip | Done |
| 2 | create_file dev-notes/BACKLOG.md | Android version on backlog |
| 3 | create_file ia_network.py | Network store |
| 4 | edit_files x4 | Layout + network + retries |
| 5 | py_compile + headless tests | All passed |
| 6 | PyInstaller rebuild + relaunch | Running |
