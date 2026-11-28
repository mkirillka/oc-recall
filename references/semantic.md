# Second search mode (semantic)

How to turn on meaning-based search, what it costs, and what it does not do.

## Why it is off by default

Word matching is precise and instant: no model, no disk, no setup. Semantic matching needs
an embedding model — 0.6 GB on disk, a few minutes to build vectors, ~30 MB added to the
index. That trade-off is not worth forcing on everyone, so it is a one-command opt-in.

## One command

```sh
oc-recall --enable-semantic
```

That is the whole procedure. The command:

1. checks that Ollama answers on `http://127.0.0.1:11434`;
2. uses the model if it is already installed;
3. otherwise asks Ollama to pull `bge-m3`;
4. if the registry is unreachable, downloads the GGUF from the Hugging Face mirror and
   registers it with `ollama create`;
5. writes `embed_model` into `~/.config/oc-recall/config.json`;
6. builds vectors for every chunk and reports progress.

Nothing is downloaded when the model is already there, so the command is safe to repeat.

## If the Ollama registry is blocked

Symptom: `ollama pull` fails with `TLS handshake timeout`, while `https://ollama.com` opens
normally. The registry (`registry.ollama.ai`) is blocked at the handshake, not missing.

`--enable-semantic` handles it: it falls back to `hf-mirror.com` automatically. To see the
manual route, or to do it by hand:

```sh
curl -L -o bge-m3-q8_0.gguf \
  "https://hf-mirror.com/ggml-org/bge-m3-Q8_0-GGUF/resolve/main/bge-m3-q8_0.gguf"
printf 'FROM ./bge-m3-q8_0.gguf\n' > Modelfile
ollama create bge-m3:mirror -f Modelfile
oc-recall --enable-semantic --semantic-model bge-m3:mirror --no-mirror
```

## Turning it off

```sh
oc-recall --disable-semantic              # keeps vectors, instant to re-enable
oc-recall --disable-semantic --drop-vectors   # frees ~30 MB
```

## Configuration

| What | Default | Override |
|---|---|---|
| Model | from `~/.config/oc-recall/config.json` | `OC_RECALL_EMBED_MODEL` |
| Ollama address | `http://127.0.0.1:11434` | `OC_RECALL_OLLAMA` |
| Settings file | `~/.config/oc-recall/config.json` | `OC_RECALL_CONFIG` |

Environment variables win over the file, so a one-off experiment does not need a config
change:

```sh
OC_RECALL_EMBED_MODEL=bge-m3 oc-recall "some query"
```

## What it changes

The final score becomes a blend:

```
score = 0.2 × min(word_cover, 1.5) + 0.8 × max(0, cosine − 0.5) / 0.5
```

Measured on 24 real questions rewritten by a local model (full table in `design.md`):

| Query style | Word matching only | With the second mode |
|---|---|---|
| English query over Russian notes | 3/24 first, 9/24 top-5 | **24/24 first, 24/24 top-5** |
| Same language, other words | 8/24 first, 11/24 top-5 | **13/24 first, 17/24 top-5** |
| Exact identifiers (`INV-2024-118`, `SKU 88213`) | correct | still correct |

The cross-language jump is the main reason to enable it: notes in two languages are common,
and word matching cannot cross that gap at all.

Hits found only by meaning (no word overlap) are flagged `"semantic": true` in `--json`.

## What it does not do

A paraphrase that shares no words *and* changes the frame ("money is tight this month" vs a
note titled "budget review") is still mostly missed — 1 of 7 in the measurement above. The
reason is granularity: a session mixes topics, so one number per session averages the signal
away. Fixing that needs per-topic grouping inside a session, which is not implemented.

## Choosing a model

Pick a model whose noise floor sits well below its true-match score. Check it before trusting
a model:

| Model | Noise floor | True match | Suitable for Russian |
|---|---|---|---|
| `bge-m3` (1024d) | 0.42–0.52 | 0.82 | yes, recommended |
| `nomic-embed-text` (768d) | 0.80–0.86 | 0.80–0.85 | no, the classes overlap |

## Verify the setup

```sh
oc-recall --status
```

Expected lines:

```
semantic:   on, 8745 chunks embedded via bge-m3:mirror
```

Then compare one query with the mode on and off:

```sh
oc-recall --disable-semantic && oc-recall "some paraphrase"
oc-recall --enable-semantic  && oc-recall "some paraphrase"
```

## Numbers

Measured on 135 sessions, ~8.7k chunks, Apple-silicon laptop:

| Step | Cost |
|---|---|
| Download the model | 0.6 GB once |
| Build vectors, 8.7k chunks | ~30 min once |
| Incremental update | under 1 s |
| Index growth | ~35 MB |
| Query | one embedding, ~200 ms |
