# Changelog

## 1.2.0 — 2026-11-28

### Added

- `--enable-semantic`: one command turns the second search mode on. It checks Ollama, uses
  an installed model, otherwise pulls it, otherwise downloads the GGUF from the Hugging Face
  mirror and registers it, saves the choice and builds vectors. Idempotent, with progress.
- `--disable-semantic` with `--drop-vectors`, `--semantic-model` and `--no-mirror`.
- `~/.config/oc-recall/config.json` stores the chosen model, so no environment variable is
  needed after setup. `OC_RECALL_EMBED_MODEL` still wins when set.
- `references/semantic.md`: procedure, mirror route, costs, verification, honest limits.

### Changed

- **Semantic scoring is now a blend, not a side signal**: `0.2 × word_cover + 0.8 ×
  max(0, cosine − 0.5) / 0.5`. Measured on 24 real questions rewritten by a local model:
  cross-language search goes from 3/24 to 24/24 at rank 1, same-language paraphrase from
  8/24 to 13/24 (top-5: 11 → 17). Exact identifiers keep ranking first.
- Very short chunks (under 8 tokens, mostly titles) are ignored as semantic evidence.
- `--status` reports the semantic state and the model actually in use.
- SKILL.md and both READMEs describe the one-command setup and the measured numbers.

### Fixed

- `--setup` and `--enable-semantic` ran the embedding step silently, so a first run looked
  hung for ~15 minutes. Progress now goes to stderr, and the output is unbuffered.
- Rejected by measurement and recorded in `references/design.md`: reciprocal rank fusion
  (9–11/24 on cross-language) and HyDE (5/24).

## 1.2.0 — 2026-11-28

### Added

- `--enable-semantic`: one command turns the second search mode on. It checks Ollama, uses
  an installed model, otherwise pulls it, otherwise downloads the GGUF from the Hugging Face
  mirror and registers it, saves the choice and builds vectors. Idempotent.
- `--disable-semantic` with optional `--drop-vectors`, `--semantic-model` and `--no-mirror`.
- `~/.config/oc-recall/config.json` stores the chosen model, so no environment variable is
  needed after setup. `OC_RECALL_EMBED_MODEL` still wins when set.
- `references/semantic.md`: procedure, mirror route, costs, verification, honest limits.
- 12 tests for the settings file and the enable/disable flow. 93 tests total.

### Changed

- `--status` reports the semantic state and the model actually in use.
- SKILL.md and both READMEs describe the one-command setup instead of asking for manual
  environment configuration.

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
