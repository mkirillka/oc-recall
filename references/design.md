# Design notes

Why lexical scoring is the default, what the optional semantic mode adds, and the numbers
behind both decisions.

## The problem

OpenCode's session picker filters on the session title. Titles are generated labels of two
to four words, so the words a user remembers ("layover", «пересадка») are often absent.
The conversations themselves contain that text.

## Two ways to match text

| | Lexical (words) | Semantic (embeddings) |
|---|---|---|
| Works when | the query and the text share words | they mean the same thing in different words |
| Fails when | the user paraphrases around the vocabulary | the model is weak in that language, or the session mixes topics |
| Cost | milliseconds, no model | seconds to minutes, a model of 0.3–2 GB |

Neither dominates, so the tool uses lexical by default and semantic as an optional add-on.

## Measurements

Real data: 104 sessions, ~7.7k chunks. Metric hit@5 — is the expected session in the top
five.

### Lexical scoring

20 paraphrased queries written against known titles: **hit@5 = 19/20, MRR 0.77**.
The single miss is a query made of digits and service words, which carries no topic.

### Embedding models, same corpus

| Model | Noise floor (cosine for an unrelated query) | Real match | Effect on lexical hits |
|---|---|---|---|
| `nomic-embed-text` (768d) | 0.80–0.86 | 0.80–0.85 | **Degrades**: unrelated text scores as high as a real match |
| `bge-m3` (1024d, q8_0) | 0.42–0.52 | 0.82 | **Neutral**: all 8 lexical hits preserved |

`nomic-embed-text` is English-centric: for Russian it returns 0.8+ similarity for text
that shares nothing. No threshold can fix a model that does not separate the classes, so it
is not recommended for Russian notes.

`bge-m3` separates the classes cleanly (noise tops out at 0.52, a true match reaches 0.82),
which is why `SEMANTIC_MIN = 0.62` is the default. It adds a signal without displacing
lexical hits.

### What semantic mode does *not* do

On four hand-written paraphrases that share no words with the notes, `bge-m3` did not turn
a miss into a hit. Two reasons:

1. **A session is not a document.** A session mixes topics, so a single vector for the whole
   session averages the signal away. Chunk-level similarity finds *some* relevant chunk, but
   ranking the right *session* needs a different aggregation (per-topic clustering or a
   summary embedding), which is not implemented yet.
2. **Paraphrase retrieval is genuinely hard.** "money is tight this month" against a session
   titled "budget review" is a reasoning step, not a similarity step.

Honest summary: semantic mode is a sound foundation and a safe opt-in, but it does not yet
deliver the "catch every paraphrase" behaviour it promises. Do not enable it expecting that.

## Scoring model (lexical)

1. **Tokens.** Lowercase, drop stop words, strip an inflection ending (11 groups, longest
   match wins), split hyphenated words into the whole token and its parts.
2. **Prefix stems.** Each token longer than five characters also contributes `~` + its
   first five characters to the index. This is what makes `путешествие` and
   `путешествовать` meet: stemming leaves `путешеств` and `путешествов`.
3. **Concepts.** One query word plus its synonyms form a concept. A concept is covered when
   any of its forms is in the chunk. This is why an English query finds Russian notes about
   the same thing, and why a synonym match counts as topic coverage rather than as a weak
   extra signal.
4. **Weights.** Weight = IDF × role: 1.0 for a topic word, 0.55 for a synonym from the
   table, 0.25 for an intent word ("discuss", "find", "plan"). A word absent from the index
   is dropped, not counted as a miss.
5. **Coverage.** `0.8 × densest chunk + 0.2 × whole session`. The chunk term keeps long
   sessions from winning on volume; the session term lets a topic spread over several
   messages count.
6. **Threshold.** A session below 34% coverage is dropped rather than shown as noise.
7. **Type boost.** Title 1.9×, user question 1.1×, answer 1.0×.
8. **Semantic add-on** (optional): `0.55 × cosine`, and a session with no lexical coverage
   still qualifies when its best cosine reaches 0.62. Such a hit is flagged `semantic`.

## Getting a multilingual model offline

`ollama pull bge-m3` fails in networks that block `registry.ollama.ai`: the TLS handshake
never completes while the website itself loads. The Hugging Face CDN is blocked in the same
networks, but the `hf-mirror.com` mirror is not:

```sh
curl -L -o bge-m3-q8_0.gguf \
  "https://hf-mirror.com/ggml-org/bge-m3-Q8_0-GGUF/resolve/main/bge-m3-q8_0.gguf"
printf 'FROM ./bge-m3-q8_0.gguf\n' > Modelfile
ollama create bge-m3:q8_0 -f Modelfile
OC_RECALL_EMBED_MODEL=bge-m3:q8_0 oc-recall --reindex
```

Indexing 7.7k chunks with this model takes about 15 minutes on an M-series laptop and adds
roughly 30 MB to the index.

## Known weaknesses

- **Paraphrase without shared words.** Not solved. See the measurements above.
- **Numbers.** A query of digits and stop words has no topic.
- **Rare word forms.** The stemming table is heuristic; a form outside it relies on the
  five-letter stem or the synonym table.
- **Intent words.** The list is finite; a query built mostly of meta words behaves
  erratically.

Every one of these has a regression test in `tests/test_oc_recall.py` or
`tests/test_quality.py`, so a change that makes them worse fails loudly.
