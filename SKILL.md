---
name: oc-recall
description: Search past OpenCode sessions by meaning instead of by session title — indexes the actual conversation text locally and answers queries like "cheap flight with a layover" or «где считали бюджет по категориям». Works offline, standard library only, single-file index. Use when the user says "find the session where we discussed…", "where did we talk about…", "which session was about…", "найди сессию про…", "где мы обсуждали…", "в какой сессии было про…", or asks to recall decisions, commands or context from earlier work that the title does not describe.
license: MIT
metadata:
  version: 1.1.1
  updated: 2026-11-21
  type: oneshot
---

# oc-recall

Search past OpenCode sessions by what was actually said in them, not by their titles.

**Main rule:** run `oc-recall` before telling the user you do not remember. The built-in
session picker matches titles only; this tool matches the conversation text.

## What it does

OpenCode stores every message in `opencode.db`. `oc-recall` reads that file read-only and
builds a separate index of normalized words:

- Russian inflection is stripped and hyphenated words are split, so word forms match;
- a five-letter prefix stem bridges forms that stay different after stemming;
- words are weighted by rarity (IDF), so a rare topic word beats a common one;
- a synonym table links English and Russian terms (flight/рейс, layover/пересадка,
  budget/бюджет);
- words that describe the asking, not the topic ("discuss", "find", "plan"), are damped
  to a quarter weight.

A session is returned only if it covers a real share of the query weight. Everything runs
locally: no network, no embedding model, no API key.

## Steps

1. **Locate the tool.** `~/.local/bin/oc-recall`, or `scripts/oc_recall.py` in the skill.
2. **First run on a new machine:** `oc-recall --setup`. It creates the data and config
   directories, writes an empty personal synonym dictionary, builds the index and installs
   the hourly updater. Nothing personal is read from or written to the repository.
3. **Search.**
   ```sh
   oc-recall "cheap flight with a layover"
   oc-recall -n 10 --json "разбор бюджета по категориям"
   oc-recall -d ~/src/project --days 30 "refactor the api layer"
   ```
4. **Report.** For each hit show title, date, relevance, one line of the snippet and the
   `ses_...` id. To continue a session, run `opencode -s <id>` or offer to open it.
5. **If nothing was found,** say so and suggest a reformulation: different words, wider
   topic, or a technical term. Never invent a session that is not in the output.

## Commands

| Command | What it does |
|---|---|
| `oc-recall "..."` | search, 5 results |
| `oc-recall -n 10` | more results |
| `oc-recall --json` | machine-readable output |
| `oc-recall -d <text>` | only sessions whose path contains the text |
| `oc-recall --days N` | only the last N days |
| `oc-recall --explain` | show how the query was parsed |
| `oc-recall --open` | open the best session in OpenCode |
| `oc-recall --setup` | first-time setup: dirs, dictionary, index, updater |
| `oc-recall --status` | index state and updater state |
| `oc-recall --sync` | incremental update |
| `oc-recall --reindex` | rebuild from scratch |
| `oc-recall --install-agent` / `--uninstall-agent` | manage the updater |

## Data and privacy

| What | Where | Goes to git? |
|---|---|---|
| Session index | `~/.local/share/oc-recall/index.db` | never |
| Personal synonyms | `~/.config/oc-recall/synonyms.json` | never |
| OpenCode database | `~/.local/share/opencode/opencode.db` | read-only, never copied |
| Updater job | `~/Library/LaunchAgents/local.oc-recall.index.plist` (macOS) or a systemd user timer | never |

The repository contains code and generic vocabulary only. Add your own machine names,
products and project jargon to the personal dictionary — not to the code.

Optional semantic mode: `OC_RECALL_EMBED_MODEL=bge-m3 oc-recall --reindex` adds an
embedding signal for paraphrases that share no words. Off by default. Measured: it keeps
every lexical hit and separates noise from real matches, but does not yet catch
hand-written paraphrases with no shared vocabulary — see `references/design.md`.

## Files

| File | Contents |
|---|---|
| `scripts/oc_recall.py` | CLI, indexer, search |
| `scripts/agent.py` | updater: launchd, systemd user timer, cron fallback |
| `tests/test_oc_recall.py` | offline tests: parsing, index, search, CLI, agent |
| `tests/test_quality.py` | synthetic bilingual corpus with expected rankings |
| `evals/evals.json` | behavioural scenarios for the agent |
| `references/design.md` | why lexical scoring, what was measured |
| `install.sh` | symlink install into the OpenCode skills directory |
