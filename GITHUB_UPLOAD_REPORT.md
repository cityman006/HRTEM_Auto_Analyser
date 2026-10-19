# HRTEM GitHub Upload Report

Date: 2026-10-19

## Result

The reviewed HRTEM Auto Analyser release was pushed to:

<https://github.com/cityman006/HRTEM_Auto_Analyser>

The local repository is on `main` and tracks `origin/main`. The verified release commit is:

`f835493b6b99f1c83ef80234cfb1fe8bf678a3ad`

Remote `refs/heads/main` resolves to the same commit after push.

## Uploaded content

25 reviewed files were committed: the Python measurement, GUI, font/export modules; Windows launchers; `requirements.txt` and the tested lock file; the sanitized PowerShell setup script; focused diagnostics and export/science validation tools; `.gitignore`; and the public English `README.md`.

The remote repository's original `.gitignore` was preserved and extended with HRTEM-specific rules.

## Excluded content

The commit excludes `.venv`, Python caches, logs, backups, generated outputs/results, local export settings, installation caches, original microscopy files (`.tif`, `.tiff`, DM3/DM4), spreadsheets, generated CSV/NPZ data, local reports, and machine-specific configuration. No token, password, `.env`, or personal absolute path was found in the reviewed upload set. No license was added; the repository remains license-pending.

## Verification

- `hrtem_spacing.py --self-test`: passed (7.2498 px measured for a 7.2500 px synthetic period).
- GUI smoke test and Python syntax compilation: passed.
- Vector/export validation: passed for Times New Roman editable SVG/PDF and Arial path SVG/PDF, with PNG/TIFF outputs and unchanged measurement CSV/summary.
- Staged file list, sizes, personal-path scan, and cached diff were inspected before commit.
- Remote `main` commit SHA and tree were read after push; no excluded data entered the branch.

## Future update workflow

Use a feature branch for each Codex change. Run the self-test and affected validation tools, inspect `git status` and `git diff --cached`, stage only reviewed source/docs/templates, commit with a focused message, push the branch, and open a pull request against `main`. Merge only after review. Use `git log --oneline --all` and `git show <commit>:<path>` to inspect or restore an earlier version; do not force-push or rewrite shared history.
