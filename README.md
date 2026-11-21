# oc-recall

> Search past OpenCode sessions by meaning, not by title. Local, offline, no models.

OpenCode keeps every session in a local SQLite file and lets you filter sessions by title
only. The title is a three-word label — it rarely contains the words you remember. So when
you ask "where did we discuss the VPN layout with an intermediate relay in Russia", the
built-in picker returns nothing, even though the conversation is right there.

`oc-recall` indexes the conversation text itself and answers that question in under a
second, offline, with no embedding model and no API key.

```
1. [2.85] 2026-11-14 — Network routing notes
   ~/src/infra
   ses_04f1c9d2ffe...  (relevance 100%, chunks 6, question)
   …the client connects to an intermediate relay in Russia, which then reaches …
```

## Features

- **Meaning first.** Russian inflection is stripped, hyphens are split, and a five-letter
  stem bridges word forms, so `умножение` finds `умножить`.
- **Bilingual.** A built-in synonym table links English and Russian technical terms:
  `vpn` ↔ `впн` ↔ `wireguard`, `relay` ↔ `релей`, `router` ↔ `роутер`.
- **Rarity-weighted.** A rare word counts more than a common one, so the topic decides the
  ranking, not the length of the session.
- **Intent words damped.** "discuss", "find", "plan", «обсуждение», «схема» weigh a quarter
  of a topic word — otherwise long sessions win on sheer volume.
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
oc-recall "vpn through an intermediate relay"
oc-recall -n 10 --json "сколько стоит подписка на модели"
oc-recall -d ~/src/infra --days 30 "epics for the quarter"
oc-recall --open "tetris in the dock panel"
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

The personal dictionary extends the built-in vocabulary with your own machine names,
products and project jargon:

```json
{
  "groups": [
    ["acme", "vpn", "впн"],
    ["myrouter", "router", "роутер"],
    ["projectx", "px"]
  ]
}
```

It is created empty on first setup with mode `600` and never leaves your machine.

## Limitations

- **Lexical, not semantic.** A full paraphrase with no shared words ("card payment
  problems" vs a session about opening an account abroad) will not be found. Local
  embedding models were measured and performed worse — see `references/design.md`.
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
