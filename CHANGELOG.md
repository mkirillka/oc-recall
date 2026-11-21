# Changelog

## 1.0.1 — 2026-11-21

### Fixed

- First run on a new machine could end in a raw SQLite traceback ("unable to open
  database file") when the OpenCode database was unreadable, locked by a checkpoint or
  not a database at all. It now prints the path, the reason and what to check.
- The source database is validated on connect, so a wrong `OPENCODE_DB` fails
  immediately instead of halfway through indexing.
- Setup validates the environment before creating directories, so a failed setup no
  longer looks half-done.

## 1.0.0 — 2026-11-21

First public release.

### Added

- `oc-recall` CLI: search past OpenCode sessions by conversation text, not by title.
- Russian stemming, hyphen splitting and five-letter prefix stems, so word forms match.
- Bilingual synonym concepts (English ↔ Russian technical vocabulary) plus a personal
  dictionary in `~/.config/oc-recall/synonyms.json`.
- IDF weighting with damped intent words ("discuss", "find", "plan", «обсуждение»).
- Incremental index with pruning of deleted messages and sessions.
- `--setup` that creates directories, the dictionary, the index and the updater.
- Hourly updater: launchd on macOS, systemd user timer on Linux, printed cron line
  elsewhere.
- Filters `--dir` and `--days`, `--json`, `--explain`, `--open`, `--status`.
- 68 offline tests and a bilingual ranking benchmark.

### Fixed

- Paths were bound as default arguments at import time, so environment overrides and
  tests silently ignored them; tests could write into the real user index.
- A query whose word form was absent from the index returned nothing even when the
  shared prefix stem matched.
- Synonym matches were scored as weak extra signal instead of covering the same concept,
  which broke English queries over Russian notes.
- Running the installed symlink crashed on `import agent`; the script path is now
  resolved with `realpath`.
- Title-only sessions were dropped by the `--days` filter because titles carry no
  timestamp.
- `--json` output contained non-serializable sets.
- `--setup` printed a successful report before failing on a missing OpenCode database.
