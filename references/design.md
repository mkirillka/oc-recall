# Design notes

Why this tool scores queries with arithmetic instead of embeddings, and what was measured
before choosing that.

## The problem

OpenCode's session picker filters on the session title. Titles are generated labels of two
to four words, so the words a user remembers ("intermediate relay", «промежуточное звено»)
are usually absent. The conversations themselves contain that text.

## Candidate approaches and measurements

Measured on 104 real sessions, 7.5k chunks, 20 paraphrased queries, metric hit@5 (is the
expected session in the top five) and MRR.

| Approach | hit@5 | Verdict |
|---|---|---|
| Embeddings, `nomic-embed-text` (768d, local) | 18/20 | Rejected: cosine scores 0.3–0.85 for unrelated text, top hits were random short messages. The model is trained mostly on English |
| Embeddings, `bge-m3` (multilingual) | — | Not evaluated: model download failed in the author's network |
| LLM reranking of the top candidates, 4B local model | 15/20 | Rejected: the model dropped correct sessions and added wrong ones |
| LLM query expansion into synonyms | — | Rejected: the output repeated the query words, adding no recall |
| **Lexical: stemming + IDF + synonym concepts** | **19/20** | **Chosen** |

Conclusion: for dense technical notes in Russian, careful lexical scoring beats small
local models. If a strong multilingual embedding model is available, it is worth
re-measuring — it is the only approach that closes paraphrases without shared words.

## Scoring model

1. **Tokens.** Lowercase, drop stop words, strip an inflection ending (11 groups, longest
   match wins), split hyphenated words into the whole token and its parts.
2. **Prefix stems.** Each token longer than five characters also contributes `~` + its
   first five characters to the index. This is what makes `умножение` and `умножить` meet:
   stemming leaves `умножен` and `умножит`.
3. **Concepts.** One query word plus its synonyms form a concept. A concept is covered when
   any of its forms is in the chunk. This is why an English query finds Russian notes about
   the same thing, and why a synonym match counts as topic coverage rather than as a weak
   extra signal.
4. **Weights.** Weight = IDF × role, where role is 1.0 for a topic word, 0.55 for a synonym
   pulled in by the table, 0.25 for an intent word ("discuss", "find", "plan").
   A word that is not in the index is dropped, not counted as a miss.
5. **Coverage.** `0.8 × densest chunk + 0.2 × whole session`. The chunk term keeps long
   sessions from winning on volume alone; the session term lets a topic spread over
   several messages still count.
6. **Threshold.** A session below 34% coverage is dropped rather than shown as noise.
7. **Type boost.** A match in the title counts 1.9×, in a user question 1.1×, in an answer
   1.0×.

## Known weaknesses

- **Paraphrase without shared words.** "card payment failed" vs a session about opening an
  account abroad: no shared token, no result.
- **Numbers.** A query of digits and stop words has no topic and matches nothing useful.
- **Rare word forms.** The stemming table is heuristic; a form outside it relies on the
  five-letter stem or the synonym table.
- **Intent words.** The list is finite. A query built mostly of meta words will behave
  erratically.

Every one of these has a regression test in `tests/test_oc_recall.py` or
`tests/test_quality.py`, so a change that makes them worse fails loudly.
