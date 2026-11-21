# Changelog

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
