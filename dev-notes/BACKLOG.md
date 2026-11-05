# IA-Interact Backlog

Working list of planned/future work. Items move to the top when active.

## Android version (planned - start later)
- Goal: a mobile (Android) version of IA-Interact with the same core flows:
  login/keys, upload (files + folders), download, repository browsing,
  create-repository form, and the IA status/queue behavior.
- Options to evaluate when work starts:
  - Kivy/KivyMD (Python; packaging via Buildozer; DnD concept becomes file picker)
  - BeeWare/Toga (Python-native widgets)
  - Flutter/React Native rewrite (shared REST/S3 logic must be ported)
  - Responsive PWA (archive.org S3 API is CORS-limited; needs a proxy - likely not viable)
- The desktop GUI is getting a wide-first layout; a vertical/stacked variant
  of that layout is the natural shape for a phone screen, so design the
  layout code to keep both shapes feasible.
- Also needed: safe credential storage on Android (Keystore), background
  upload service, and storage-access-framework folder pickers.

## CLI parity flags (backlog)
- Expose `--retries N` (0=unlimited), `--skip-existing`, `--derive/--no-derive`
  as CLI flags so both front-ends match (see FEATURE-PARITY.md).
- Confirm whether the CLI's create `_rules.conf` with `CAT.ALL` currently
  disables derivatives (per IA docs `CAT.all` = disable all) and make the
  CLI's create flow explicit about derive intent.

## GUI layout rework (in progress)
- Wide-first layout: command bar (repository + target + actions) on top,
  tall side-by-side file lists, compact bottom info strip.
- Keep a vertical/stacked shape feasible for the future Android version.
