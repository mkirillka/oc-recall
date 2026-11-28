# Design notes

Why lexical scoring is the default, what the second (semantic) mode adds, and the numbers
behind both decisions.

## The problem

OpenCode's session picker filters on the session title. Titles are generated labels of two
to four words, so the words a user remembers ("layover", «пересадка») are often absent.
The conversations themselves contain that text.

## Two ways to match text

| | Word matching (lexical) | Meaning (embeddings) |
|---|---|---|
| Works when | the query and the notes share words | they mean the same thing in different words |
| Fails when | the user paraphrases around the vocabulary | the model is weak in that language |
| Cost | milliseconds, no model | one embedding per query, 0.6 GB model |

Word matching is the default because it is exact, instant and needs nothing. The semantic
mode is a one-command opt-in for the cases word matching cannot reach.

## Measurements

Real data: 135 sessions, ~8.7k chunks, Apple-silicon laptop (M-series). Metric: hit@1 / hit@5 — is the
expected session first / in the top five.

### The test that matters

Twenty-four real questions were rewritten by a local model two ways: a topic restated in
other Russian words, and a translation into English. Rewrites that kept any original word
were recorded, so their effect is visible instead of hidden.

| Query style | Word matching | Meaning only | Blend (shipped) |
|---|---|---|---|
| Translation to English (24) | 3 / 9 | 24 / 24 | **24 / 24** |
| Topic in other words (24) | 8 / 11 | 12 / 14 | **13 / 17** |
| Topic, no shared words at all (7) | 0 / 2 | 1 / 2 | 1 / 2 |

Read the first row carefully: a Russian note base searched with an English query goes from
3/24 to 24/24. That is the single biggest reason to enable the second mode — mixed-language
notes are common and word matching cannot touch them.

The third row is the honest limit: a paraphrase that shares nothing and changes the frame
("money is tight" vs "budget review") is still mostly missed. Two reasons:

1. **A session is not a document.** A session mixes topics, so any single number per session
   averages the signal away. Chunk-level similarity finds *some* relevant chunk, but ranking
   the right *session* needs per-topic grouping inside a session, which is not implemented.
2. **Paraphrase retrieval is a reasoning step**, not a similarity step.

### Blend weights

`score = 0.2 × min(word_cover, 1.5) + 0.8 × max(0, cos − 0.5) / 0.5`

Weights were chosen by sweeping the full range on both query styles. A few points from the
sweep, same-language set:

| word share | meaning share | hit@1 | hit@5 |
|---|---|---|---|
| 1.0 | 0.0 | 8/24 | 11/24 |
| 0.5 | 0.5 | 11/24 | 14/24 |
| 0.2 | 0.8 | 13/24 | 17/24 |
| 0.0 | 1.0 | 12/24 | 14/24 |

The floor at 0.5 matters: without it, a noisy model drags unrelated sessions above an exact
word match. With it, identifiers stay put — `INV-2024-118`, `SKU 88213`, `hotel Ibis` and
`price_per_night` all return the right session at rank 1 with the semantic mode on.

Very short chunks (fewer than 8 tokens) are ignored as semantic evidence: a three-word title
carries no meaning, but it would win any similarity contest.

### Rejected: rank fusion and HyDE

| Approach | Result | Verdict |
|---|---|---|
| Reciprocal rank fusion (k=10…120) | 9–11/24 on translation | Worse than the weighted blend: when one branch has no signal, its ranks are noise |
| HyDE (the model writes a hypothetical answer, search by it) | 5/24 | Worse than searching the question; the small model invents plausible but wrong context |

### Model choice

| Model | Noise floor (cosine, unrelated text) | True match | Verdict |
|---|---|---|---|
| `nomic-embed-text` (768d) | 0.80–0.86 | 0.80–0.85 | Unusable for Russian: the classes overlap |
| `bge-m3` (1024d, q8_0) | 0.42–0.52 | 0.82 | Chosen; the 0.5 floor sits in the gap |

A smaller model looks tempting for a faster first build, but `multilingual-e5-small` (384d,
131 MB) measured *slower* here — 530 ms per chunk against 200 ms for `bge-m3` — because the
GGUF runs without batching in this Ollama build. Fewer parameters did not mean less time, so
`bge-m3` stays the default.

## Scoring model (word matching)

1. **Tokens.** Lowercase, drop stop words, strip an inflection ending (11 groups, longest
   match wins), split hyphenated words into the whole token and its parts.
2. **Prefix stems.** Each token longer than five characters also contributes `~` + its first
   five characters. This is what makes `путешествие` and `путешествовать` meet.
3. **Concepts.** One query word plus its synonyms form a concept. A concept is covered when
   any of its forms is in the chunk — so a synonym counts as topic coverage, not as a weak
   extra signal.
4. **Weights.** IDF × role: 1.0 topic, 0.55 synonym from the table, 0.25 intent word. A word
   absent from the index is dropped, not counted as a miss.
5. **Coverage.** `0.8 × densest chunk + 0.2 × whole session`.
6. **Threshold.** Below 34% coverage a session is dropped rather than shown as noise.
7. **Type boost.** Title 1.9×, user question 1.1×, answer 1.0×.

## Known weaknesses

- **Paraphrase with no shared words.** Improved but not solved — see the third row above.
- **Numbers.** A query of digits and stop words has no topic.
- **Rare word forms.** The stemming table is heuristic.
- **Intent words.** The list is finite.

Each has a regression test in `tests/test_oc_recall.py` or `tests/test_quality.py`, so a
change that makes them worse fails loudly.
