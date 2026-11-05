# Prompt Log — Fix issue #6: env-var S3 keys not working in Windows .exe (CLI)

- Date: 2026-11-05
- Task: Fix https://github.com/harrypm/IA-Interact/issues/6
- Working directory: `C:\Users\Harry\IA-Interact` (branch: `main`)

## User input (original request)

> fix this bug
> https://github.com/harrypm/IA-Interact/issues/6

## Bug (from issue #6)

On Windows 11, running the pre-built `ia-interact-windows-x86_64.exe`, the in-app
help said to put the IA access/secret keys into "Environment Variables" but the
keys still weren't picked up. The GUI account manager worked. The reporter also
mentioned "(PATH)", i.e. they likely pasted keys into the PATH variable or never
learned the real variable names.

## Root causes found (hard data)

1. CLI read only `S3_ACCESS_KEY` / `S3_SECRET_KEY` via `os.getenv` in 4 places
   (`upload_file_with_progress`, `delete_file`, `move_file`, `initialize_repository`).
2. Help text and error messages never named the variables; README section 4 was
   Unix-only (`export` + `~/.bashrc`), no Windows instructions at all.
3. The CLI ignored the GUI's saved account profiles
   (`~/.config/ia-interact/accounts.json`), so keys entered once in the GUI
   didn't help the CLI/.exe.

## Changes made

- `ia-interact.py`:
  - New `resolve_ia_credentials()` + helpers (`CREDENTIAL_PAIRS`,
    `_env_credential_pair`, `_saved_profile_credential_pair`,
    `_print_missing_credentials_error`). Lookup order:
    S3_* env vars -> IA_* env vars (archive.org tooling aliases) -> active saved
    GUI profile (also mirrored into os.environ). On failure prints actionable
    per-OS instructions (setx / PowerShell / GUI editor / export), explicitly
    warns keys must NOT go into PATH, and links the key page.
  - All 4 call sites switched to the resolver.
  - `print_help()` now names the exact variables, gives Windows/macOS/Linux
    setup lines, PATH warning, and the GUI-profile fallback.
- `README.md`:
  - Section 4: exact variable names + alias note, PATH warning, three Windows
    options (GUI editor, `setx`, PowerShell session-only), re-open-terminal
    note, PowerShell echo test lines.
  - Section 9: CLI reuses the active saved GUI profile when env vars are absent.
  - Troubleshooting: rewritten env-var bullet for Windows + exact names.

## Verification

- `py_compile` on `ia-interact.py` and `ia_accounts.py`: OK.
- Functional tests of `resolve_ia_credentials()` (run with Python 3.12.10,
  `XDG_CONFIG_HOME` isolated to a temp dir so the real account store was never
  touched): missing-keys error message, S3_* pair, IA_* alias pair, half-set
  pair error, saved-profile fallback, env-over-profile priority — ALL PASSED.
- Note: the very first test run (no isolation) returned the fallback profile
  from the REAL store, which incidentally proved the fallback works on this machine.

## Commands run

| # | Command | Result |
|---|---------|--------|
| 1 | `gh issue view 6 --repo harrypm/IA-Interact` | Bug details + screenshot link |
| 2 | `curl.exe -L -o $env:TEMP\issue6_screenshot.png <asset>` | Downloaded (13 KB PNG) |
| 3 | grep/read of `ia-interact.py`, `ia_accounts.py`, `ia_interact_gui.py`, `README.md` | Located all credential call sites |
| 4 | `edit_files` x3 | Resolver + call sites + help text + README |
| 5 | `python -m py_compile ia-interact.py ia_accounts.py` | OK |
| 6 | Resolver test script (isolated) | 6/6 passed |

## Status

- Fix implemented and unit-verified; NOT yet committed/pushed (awaiting user
  confirmation). Real-world .exe verification pending (needs a new PyInstaller build).
