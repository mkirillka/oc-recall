# Changelog

## 1.1.1 — 2026-11-22

### Fixed

- `install.sh` still suggested a private-domain example in its final line.
- CI now fails when a personal topic leaks into docs or fixtures, not only when a path
  or a credential does.

## 1.1.0 — 2026-11-22

### Added

- Optional semantic mode: set `OC_RECALL_EMBED_MODEL` to an embedding model installed in
  Ollama, and a cosine signal is added to the lexical score. Hits found purely by meaning
  are flagged `semantic`. Off by default; no network use when disabled.
- `OC_RECALL_OLLAMA` to point at a non-default Ollama address.
- 7 new tests covering the semantic path with a substituted model, so the suite stays
  offline. 81 tests total.
- Measured comparison of two multilingual models on 104 real sessions, including the
  offline route for getting `bge-m3` when the Ollama registry is unreachable
  (`references/design.md`).

### Changed

- All examples, fixtures and eval scenarios use neutral domains (budget, travel,
  programming) instead of one person's private projects.
- The built-in synonym table covers money, travel and general engineering vocabulary.
- `README.md` and `README.ru.md` state plainly that paraphrases without shared words are
  not caught by the default lexical mode.

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
