# oc-recall

> Search past OpenCode sessions by meaning, not by title. Local, offline, no models.

OpenCode keeps every session in a local SQLite file and lets you filter sessions by title
only. The title is a three-word label — it rarely contains the words you remember. So when
you ask "where did we work out the flight with a layover to Lisbon", the built-in picker
returns nothing, even though the conversation is right there.

`oc-recall` indexes the conversation text itself and answers that question in under a
second, offline, with no embedding model and no API key.

```
1. [2.41] 2026-11-14 — Flights to Lisbon
   ~/src/travel
   ses_04f1c9d2ffe...  (relevance 100%, chunks 4, question)
   …cheapest flight to Lisbon has one layover in Istanbul, a third below the direct …
```

## Features

- **Word forms collapse.** Russian inflection is stripped, hyphens are split, and a
  five-letter stem bridges the rest, so `путешествие` finds `путешествовать`.
- **Bilingual.** A built-in synonym table links English and Russian terms:
  `flight` ↔ `рейс` ↔ `авиабилет`, `layover` ↔ `пересадка`, `budget` ↔ `бюджет`.
- **Rarity-weighted.** A rare word counts more than a common one, so the topic decides the
  ranking, not the length of the session.
- **Intent words damped.** "discuss", "find", "plan", «обсуждение», «разбор» weigh a
  quarter of a topic word — otherwise long sessions win on sheer volume.
- **Incremental index.** New and edited messages are re-indexed; deleted ones are dropped.
  Full rebuild of ~7.5k chunks takes ~7 s, incremental update under 1 s.
- **Nothing to learn.** One command, five results, `--json` for scripts.

## Install

```sh
git clone https://github.com/<owner>/oc-recall.git ~/.config/opencode/skills/oc-recall
cd ~/.config/opencode/skills/oc-recall
./install.sh
```

The installer symlinks the skill, creates the `oc-recall` command in `~/.local/bin`, and
runs `--setup`: it builds the index and installs an hourly updater (launchd on macOS,
systemd user timer on Linux, printed cron line elsewhere).

Requirements: Python 3.9+, standard library only. No network access at any point.

Manual setup, if you prefer:

```sh
python3 scripts/oc_recall.py --setup        # dirs + dictionary + index + updater
python3 scripts/oc_recall.py --no-agent     # or skip the updater
```

## Quick start

```sh
oc-recall "cheap flight with a layover"
oc-recall -n 10 --json "разбор бюджета по категориям"
oc-recall -d ~/src/travel --days 30 "hotel with free cancellation"
oc-recall --open "refactor the api layer"
```

## Commands

| Command | What it does |
|---|---|
| `oc-recall "..."` | search, 5 results |
| `-n, --limit N` | number of results |
| `--json` | JSON output for scripts |
| `-d, --dir TEXT` | only sessions whose path contains TEXT |
| `--days N` | only the last N days |
| `--explain` | show how the query was parsed into concepts |
| `--open` | open the best session in OpenCode |
| `--setup` | first-time setup |
| `--status` | index and updater state |
| `--sync` | incremental update |
| `--reindex` | rebuild from scratch |
| `--enable-semantic` | second search mode: ensure a model, save config, build vectors |
| `--disable-semantic` | turn the second mode off (`--drop-vectors` to free space) |
| `--install-agent` / `--uninstall-agent` | manage the hourly updater |

Exit codes: `0` found, `1` nothing found, `2` bad usage, `3` environment problem
(no OpenCode database, empty index).

## Configuration

| What | Default | Override |
|---|---|---|
| Index file | `~/.local/share/oc-recall/index.db` | `OC_RECALL_INDEX` |
| Personal dictionary | `~/.config/oc-recall/synonyms.json` | `OC_RECALL_DICT` |
| OpenCode database | `~/.local/share/opencode/opencode.db` | `OPENCODE_DB` |
| Auto-sync threshold | 300 s | `OC_RECALL_AUTO_SYNC` |
| Semantic mode | off | `OC_RECALL_EMBED_MODEL` |
| Ollama address | `http://127.0.0.1:11434` | `OC_RECALL_OLLAMA` |

### Second search mode

Word matching is blind to a paraphrase with no shared words. A multilingual embedding model
covers part of that gap. One command does the whole setup:

```sh
oc-recall --enable-semantic
```

It checks Ollama, uses the model if installed, otherwise pulls it, otherwise downloads the
GGUF from the Hugging Face mirror (the Ollama registry is blocked on some networks) and
registers it, saves the choice in `~/.config/oc-recall/config.json`, then builds vectors.
Indexing costs ~30 minutes for 8.7k chunks on an M-series laptop, once; then ~1 s per
update. The index grows by ~35 MB. Hits found purely by meaning are flagged `semantic` in `--json` output. Details:
`references/semantic.md`.

Measured on 24 real questions rewritten by a local model:

| Query style | words only | with the second mode |
|---|---|---|
| English query over Russian notes | 3/24 first, 9/24 top-5 | **24/24 first** |
| Same language, other words | 8/24 first, 11/24 top-5 | **13/24 first, 17/24 top-5** |
| Exact identifiers | correct | still correct |

The cross-language gain is the headline: notes written in two languages become searchable
from either side. A paraphrase that changes the frame ("money is tight" vs "budget review")
is still mostly missed — that is the honest limit. Raw numbers and the rejected approaches
(rank fusion, HyDE): `references/design.md`.

The personal dictionary extends the built-in vocabulary with your own machine names,
products and project jargon:

```json
{
  "groups": [
    ["projectx", "px"],
    ["acme", "acme-corp"]
  ]
}
```

It is created empty on first setup with mode `600` and never leaves your machine.

## Limitations

- **Lexical by default.** A full paraphrase with no shared words ("money is tight this
  month" vs a session titled "budget review") is not found unless the words overlap or a
  synonym group covers them. See `references/design.md` for the measurements and for the
  optional embedding mode.
- **OpenCode only.** The indexer reads the OpenCode database schema; other tools need
  their own adapter.
- **Russian stemming is heuristic.** Eleven ending groups cover the common cases; exotic
  forms may still miss, which is what the synonym table is for.
- **The built-in session picker is not replaced.** This is a separate CLI and a skill
  instruction; the TUI picker would need a patched binary.
- **Numbers-only queries** rarely match, since digits carry no topic.

## Tests

```sh
python3 tests/test_oc_recall.py     # 73 offline tests
python3 tests/test_quality.py       # bilingual ranking benchmark
```

Both run offline against a synthetic database created in a temporary directory. They never
touch your real index.

## Privacy

The repository ships code and generic vocabulary only. The index, the personal dictionary
and the updater job live outside the repository and are ignored by `.gitignore`.

## License

MIT.
