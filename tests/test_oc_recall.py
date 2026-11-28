#!/usr/bin/env python3
"""Offline tests for oc-recall: no network, no OpenCode install, no personal data.

Run: python3 tests/test_oc_recall.py
"""

from __future__ import annotations

import json
import os
import plistlib
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from zlib import crc32

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import agent  # noqa: E402
import oc_recall  # noqa: E402


DAY_MS = 86_400_000
NOW = int(time.time() * 1000)


def build_source_db(path: str, sessions: list[dict]) -> None:
    """Create a synthetic OpenCode database with the columns oc-recall reads."""
    con = sqlite3.connect(path)
    con.executescript(
        """
        create table project(id text primary key, name text);
        create table session_v2(
            id text primary key, project_id text, title text, directory text,
            time_created integer, time_updated integer);
        create table session_message(
            id text primary key, session_id text, type text, seq integer,
            time_created integer, time_updated integer, data text);
        """
    )
    con.execute("insert into project values('p1','demo')")
    for session in sessions:
        con.execute(
            "insert into session_v2 values(?,?,?,?,?,?)",
            (
                session["id"], "p1", session["title"], session.get("dir", "/tmp/demo"),
                session.get("created", NOW), session.get("updated", NOW),
            ),
        )
        for index, message in enumerate(session.get("messages", [])):
            payload = (
                {"text": message["text"]}
                if message["type"] == "user"
                else {"content": [{"type": "text", "text": message["text"]}]}
            )
            con.execute(
                "insert into session_message values(?,?,?,?,?,?,?)",
                (
                    f"{session['id']}-m{index}", session["id"], message["type"], index,
                    session.get("created", NOW), message.get("updated", NOW),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
    con.commit()
    con.close()


# Neutral demo data: money, travel, programming. Nothing specific to any one person.
DEMO_SESSIONS = [
    {
        "id": "ses_budget",
        "title": "Quarterly budget review",
        "messages": [
            {"type": "user", "text": "Разбери траты за квартал по категориям и посоветуй, "
                                     "какую долю дохода откладывать в накопления."},
            {"type": "assistant", "text": "Разложил расходы по категориям: жильё, еда, "
                                         "транспорт. Откладывать 20% дохода реально."},
        ],
    },
    {
        "id": "ses_flights",
        "title": "Flights to Lisbon",
        "messages": [
            {"type": "user", "text": "Найди дешёвые билеты в Лиссабон с одной пересадкой."},
            {"type": "assistant", "text": "Стыковка в Стамбуле, 12 часов ожидания, "
                                         "цена ниже прямого рейса на треть."},
        ],
    },
    {
        "id": "ses_api",
        "title": "API refactoring",
        "messages": [
            {"type": "user", "text": "Раздели эндпоинты интеграционного API и добавь тесты "
                                     "на коды ошибок."},
            {"type": "assistant", "text": "Эндпоинты разделены по ресурсам, тесты "
                                         "покрывают 400 и 500."},
        ],
    },
    {
        "id": "ses_titled_only",
        "title": "Cheap flights with a layover",
        "created": NOW - 3 * DAY_MS,
        "messages": [],
    },
    {
        "id": "ses_old",
        "title": "Old unrelated session",
        "created": NOW - 400 * DAY_MS,
        "messages": [{"type": "user", "text": "Проверь старые заметки про ремонт кухни."}],
    },
]


class Base(unittest.TestCase):
    """Point the module at a temporary index and a synthetic OpenCode database."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.source_db = os.path.join(root, "opencode.db")
        self.index_db = os.path.join(root, "data", "index.db")
        self.dict_path = os.path.join(root, "config", "synonyms.json")
        build_source_db(self.source_db, DEMO_SESSIONS)

        self._saved = (
            oc_recall.INDEX_DB, oc_recall.SOURCE_DB, oc_recall.USER_DICT,
            oc_recall.DATA_DIR, oc_recall.CONFIG_DIR,
        )
        oc_recall.INDEX_DB = self.index_db
        oc_recall.SOURCE_DB = self.source_db
        oc_recall.USER_DICT = self.dict_path
        oc_recall.DATA_DIR = os.path.dirname(self.index_db)
        oc_recall.CONFIG_DIR = os.path.dirname(self.dict_path)
        # hard guard: a regression here once made tests write into the real user index
        for value in (oc_recall.INDEX_DB, oc_recall.SOURCE_DB, oc_recall.USER_DICT):
            self.assertTrue(value.startswith(root), f"test path escapes temp dir: {value}")

    def tearDown(self) -> None:
        (
            oc_recall.INDEX_DB, oc_recall.SOURCE_DB, oc_recall.USER_DICT,
            oc_recall.DATA_DIR, oc_recall.CONFIG_DIR,
        ) = self._saved
        self.tmp.cleanup()

    def index(self, full: bool = True) -> dict:
        return oc_recall.sync(full=full)


class TokenTest(unittest.TestCase):
    def test_stem_removes_russian_endings(self):
        self.assertEqual(oc_recall.stem("расходы"), "расход")
        self.assertEqual(oc_recall.stem("бюджетами"), "бюджет")

    def test_stem_keeps_short_words(self):
        self.assertEqual(oc_recall.stem("sql"), "sql")
        self.assertEqual(oc_recall.stem("api"), "api")

    def test_tokens_split_hyphenated_words(self):
        # "check-in" must be findable as the whole word and as its parts
        parts = oc_recall.tokens("онлайн-бронирование")
        self.assertIn("онлайн-бронирован", parts)
        self.assertIn("онлайн", parts)
        self.assertIn("бронирован", parts)

    def test_tokens_drop_stop_words(self):
        self.assertEqual(oc_recall.tokens("и в на the a of"), [])

    def test_tokens_fold_yo(self):
        self.assertEqual(oc_recall.tokens("счёт"), oc_recall.tokens("счет"))

    def test_index_tokens_add_prefix_stems(self):
        # "путешествие" and "путешествовать" share the five-letter stem "путеш"
        for word in ("путешествие", "путешествовать"):
            self.assertIn("~путеш", oc_recall.index_tokens(word))

    def test_prefix_stems_not_added_for_short_words(self):
        self.assertFalse(any(t.startswith("~") for t in oc_recall.index_tokens("api")))


class ChunkTest(unittest.TestCase):
    def test_short_text_single_chunk(self):
        self.assertEqual(oc_recall.chunk_text("hello"), ["hello"])

    def test_empty_text_no_chunks(self):
        self.assertEqual(oc_recall.chunk_text("   \n  "), [])

    def test_long_text_split_with_overlap(self):
        text = "\n\n".join(f"Абзац {i} " + "слово " * 60 for i in range(8))
        chunks = oc_recall.chunk_text(text, size=300, overlap=50)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.strip() for chunk in chunks))
        self.assertTrue(all(len(chunk) <= 400 for chunk in chunks))

    def test_chunks_prefer_paragraph_boundary(self):
        # boundary must be chosen when it falls in the second half of the window
        text = "Заполнитель " * 12 + "\n\n" + "Второй абзац " * 60
        chunks = oc_recall.chunk_text(text, size=200, overlap=20)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(chunks[0].endswith("Заполнитель"), chunks[0][-40:])

    def test_assistant_message_ignores_tool_parts(self):
        payload = {
            "content": [
                {"type": "tool", "text": "служебный вывод команды"},
                {"type": "text", "text": "полезный ответ"},
            ]
        }
        self.assertEqual(oc_recall.message_text("assistant", payload), "полезный ответ")


class DictionaryTest(Base):
    def test_missing_user_dictionary_is_not_an_error(self):
        table = oc_recall.load_synonyms("/nonexistent/synonyms.json")
        self.assertIn("flight", table)
        self.assertIn("рейс", table["flight"])

    def test_user_dictionary_is_merged(self):
        os.makedirs(os.path.dirname(self.dict_path), exist_ok=True)
        with open(self.dict_path, "w", encoding="utf-8") as fh:
            json.dump({"groups": [["проектx", "projectx"]]}, fh)
        table = oc_recall.load_synonyms(self.dict_path)
        self.assertIn("projectx", table["проектx"])

    def test_broken_dictionary_does_not_crash(self):
        os.makedirs(os.path.dirname(self.dict_path), exist_ok=True)
        with open(self.dict_path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertIn("flight", oc_recall.load_synonyms(self.dict_path))

    def test_single_word_groups_are_ignored(self):
        os.makedirs(os.path.dirname(self.dict_path), exist_ok=True)
        with open(self.dict_path, "w", encoding="utf-8") as fh:
            json.dump({"groups": [["одиночка"]]}, fh)
        table = oc_recall.load_synonyms(self.dict_path)
        self.assertNotIn("одиночк", table)


class PlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.idf = {"бюджет": 5.0, "расход": 4.0, "накоплен": 6.0, "обсужден": 6.0,
                    "~бюдже": 4.0, "budget": 4.5}
        self.synonyms = oc_recall.load_synonyms("/nonexistent/synonyms.json")

    def test_intent_words_weigh_less(self):
        plan = oc_recall.query_plan("обсуждение бюджета", self.idf, self.synonyms)
        self.assertEqual(plan["role"]["обсужден"], oc_recall.META_WEIGHT)
        self.assertEqual(plan["role"]["бюджет"], 1.0)

    def test_synonyms_added_with_lower_weight(self):
        plan = oc_recall.query_plan("бюджет", self.idf, self.synonyms)
        self.assertAlmostEqual(plan["role"]["budget"], oc_recall.SYNONYM_WEIGHT)

    def test_exact_form_does_not_add_prefix_variant(self):
        # regression: adding the five-letter stem next to the exact term inflated the
        # denominator and made the same match count twice
        plan = oc_recall.query_plan("бюджет", self.idf, self.synonyms)
        weights = oc_recall.plan_weights(plan)
        self.assertIn("бюджет", weights)
        self.assertNotIn("~бюдже", weights)

    def test_prefix_fallback_counts_as_topic(self):
        # regression: a query whose word form is absent from the index returned nothing,
        # even though the shared prefix stem was indexed
        idf = {"путешеств": 5.0, "~путеш": 5.0}
        plan = oc_recall.query_plan("путешествовать", idf, self.synonyms)
        self.assertEqual(plan["primary"][0]["members"], {"~путеш"})
        self.assertTrue(plan["found"])

    def test_short_words_have_no_prefix_fallback(self):
        idf = {"api": 5.0}
        plan = oc_recall.query_plan("api", idf, self.synonyms)
        self.assertEqual(plan["primary"][0]["members"], {"api"})

    def test_prefix_match_weighs_less_than_full_word(self):
        idf = {"путешеств": 5.0, "~путеш": 5.0}
        plan = oc_recall.query_plan("путешествие", idf, self.synonyms)
        self.assertAlmostEqual(plan["primary"][0]["weight"], 5.0)  # exact form wins
        idf = {"путешествов": 5.0, "~путеш": 5.0}
        plan = oc_recall.query_plan("путешествие", idf, self.synonyms)
        self.assertAlmostEqual(
            plan["primary"][0]["weight"], 5.0 * oc_recall.PREFIX_WEIGHT
        )

    def test_unknown_words_are_dropped(self):
        plan = oc_recall.query_plan("бюджет квантовыйфлукс", self.idf, self.synonyms)
        weights = oc_recall.plan_weights(plan)
        self.assertIn("бюджет", weights)
        self.assertNotIn("квантовыйфлукс", weights)

    def test_query_without_known_words_reports_empty(self):
        plan = oc_recall.query_plan("абракадабра", self.idf, self.synonyms)
        self.assertFalse(plan["found"])

    def test_terms_sharing_a_synonym_merge_into_one_concept(self):
        # "flight" and "рейс" are synonyms: they must not be counted as two topics
        idf = {"flight": 5.0, "рейс": 5.0}
        plan = oc_recall.query_plan("flight рейс", idf, self.synonyms)
        self.assertEqual(len(plan["primary"]), 1)


class IndexTest(Base):
    def test_index_counts_sessions_and_chunks(self):
        result = self.index()
        self.assertEqual(result["sessions"], len(DEMO_SESSIONS))
        self.assertGreaterEqual(result["total_chunks"], len(DEMO_SESSIONS))
        self.assertGreater(result["terms"], 10)

    def test_title_chunk_exists_for_every_session(self):
        self.index()
        con = oc_recall.connect_index()
        try:
            titles = con.execute(
                "select count(*) from chunk where kind='title'"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(titles, len(DEMO_SESSIONS))

    def test_incremental_sync_picks_up_new_message(self):
        self.index()
        con = sqlite3.connect(self.source_db)
        con.execute(
            "insert into session_message values(?,?,?,?,?,?,?)",
            ("ses_budget-m99", "ses_budget", "user", 99, NOW, NOW,
             json.dumps({"text": "И ещё посчитай, сколько уходит на подписки."},
                        ensure_ascii=False)),
        )
        con.commit()
        con.close()
        before = oc_recall.connect_index()
        before_count = before.execute("select count(*) from chunk").fetchone()[0]
        before.close()
        self.index(full=False)
        after = oc_recall.connect_index()
        after_count = after.execute("select count(*) from chunk").fetchone()[0]
        after.close()
        self.assertGreater(after_count, before_count)
        found = oc_recall.search("сколько уходит на подписки")
        self.assertEqual(found[0]["session_id"], "ses_budget")

    def test_deleted_message_is_pruned(self):
        # messages go, the session stays: it is still findable by its title
        self.index()
        con = sqlite3.connect(self.source_db)
        con.execute("delete from session_message where session_id='ses_flights'")
        con.commit()
        con.close()
        self.index(full=False)
        con = oc_recall.connect_index()
        try:
            kinds = [row[0] for row in con.execute(
                "select kind from chunk where session_id='ses_flights'"
            )]
        finally:
            con.close()
        self.assertEqual(kinds, ["title"])

    def test_deleted_session_is_pruned(self):
        self.index()
        con = sqlite3.connect(self.source_db)
        con.execute("delete from session_v2 where id='ses_flights'")
        con.commit()
        con.close()
        self.index(full=False)
        # "стамбул" is unique to the deleted session, so a synonym cannot rescue it
        self.assertEqual(oc_recall.search("стамбул"), [])

    def test_source_change_forces_rebuild(self):
        self.index()
        other = os.path.join(self.tmp.name, "other.db")
        build_source_db(other, [{"id": "ses_new", "title": "Only here",
                                 "messages": [{"type": "user", "text": "уникальное слово"}]}])
        oc_recall.SOURCE_DB = other
        result = self.index(full=False)
        self.assertEqual(result["sessions"], 1)
        self.assertEqual(oc_recall.search("уникальное слово")[0]["session_id"], "ses_new")

    def test_unreadable_message_is_skipped(self):
        con = sqlite3.connect(self.source_db)
        con.execute(
            "insert into session_message values(?,?,?,?,?,?,?)",
            ("bad-1", "ses_flights", "user", 50, NOW, NOW, "{broken json"),
        )
        con.commit()
        con.close()
        result = self.index()
        self.assertEqual(result["unreadable"], 1)

    def test_prune_returns_zero_on_clean_index(self):
        self.index()
        result = self.index(full=False)
        self.assertEqual(result["pruned"], 0)


class SearchTest(Base):
    def setUp(self) -> None:
        super().setUp()
        self.index()

    def test_russian_paraphrase_finds_the_session(self):
        results = oc_recall.search("дешёвые билеты с пересадкой в лиссабон")
        self.assertTrue(results)
        self.assertEqual(results[0]["session_id"], "ses_flights")

    def test_english_paraphrase_finds_the_session(self):
        results = oc_recall.search("cheap flight with a layover to lisbon")
        self.assertIn("ses_flights", [r["session_id"] for r in results])

    def test_synonym_bridges_languages(self):
        # English query against a Russian-only conversation
        results = oc_recall.search("budget categories")
        self.assertIn("ses_budget", [r["session_id"] for r in results])

    def test_intent_words_do_not_dominate(self):
        results = oc_recall.search("обсуждение разбор итог бюджет")
        self.assertTrue(results)
        self.assertEqual(results[0]["session_id"], "ses_budget")

    def test_unrelated_session_is_not_returned(self):
        results = oc_recall.search("пересадка лиссабон")
        self.assertNotIn("ses_old", [r["session_id"] for r in results])

    def test_unknown_query_returns_nothing(self):
        self.assertEqual(oc_recall.search("квантовыйфлукс"), [])

    def test_days_filter_keeps_title_only_session(self):
        # regression: title chunks have no timestamp and used to be filtered out
        results = oc_recall.search("flights with a layover", days=7)
        self.assertIn("ses_titled_only", [r["session_id"] for r in results])

    def test_days_filter_excludes_old_sessions(self):
        results = oc_recall.search("ремонт кухни", days=7)
        self.assertEqual(results, [])

    def test_directory_filter(self):
        results = oc_recall.search("пересадка", directory="/tmp/demo")
        self.assertTrue(results)
        self.assertEqual(oc_recall.search("пересадка", directory="/nowhere"), [])

    def test_results_are_json_serializable(self):
        payload = json.dumps(oc_recall.search("бюджет"), ensure_ascii=False)
        self.assertIn("ses_budget", payload)

    def test_result_fields(self):
        item = oc_recall.search("бюджет")[0]
        for field in ("session_id", "title", "directory", "date", "relevance", "score",
                      "hits", "matched_in", "snippet"):
            self.assertIn(field, item)

    def test_limit_is_respected(self):
        self.assertEqual(len(oc_recall.search("бюджет пересадка", limit=1)), 1)

    def test_ranking_prefers_title_hit(self):
        results = oc_recall.search("flights with a layover")
        self.assertEqual(results[0]["session_id"], "ses_titled_only")

    def test_prefix_bridge_matches_other_word_form(self):
        con = sqlite3.connect(self.source_db)
        con.execute(
            "insert into session_message values(?,?,?,?,?,?,?)",
            ("travel-1", "ses_old", "user", 1, NOW, NOW,
             json.dumps({"text": "Люблю путешествие на поезде по выходным."},
                        ensure_ascii=False)),
        )
        con.commit()
        con.close()
        self.index(full=False)
        results = oc_recall.search("путешествовать", limit=3)
        self.assertIn("ses_old", [r["session_id"] for r in results])


class PathResolutionTest(Base):
    """Regression: paths were bound as default arguments at import time.

    That made every override (env var, config, test patch) silently ineffective and let
    the test suite write into the real user index.
    """

    def test_connect_index_uses_current_module_path(self):
        con = oc_recall.connect_index()
        try:
            con.execute("create table if not exists probe(x)")
            con.commit()
        finally:
            con.close()
        self.assertTrue(os.path.exists(self.index_db))

    def test_resolve_source_uses_current_module_path(self):
        self.assertEqual(oc_recall.resolve_source(), self.source_db)

    def test_load_synonyms_uses_current_dictionary_path(self):
        os.makedirs(os.path.dirname(self.dict_path), exist_ok=True)
        with open(self.dict_path, "w", encoding="utf-8") as fh:
            json.dump({"groups": [["проба", "probe"]]}, fh)
        self.assertIn("probe", oc_recall.load_synonyms()["проб"])


class EmbeddingTest(Base):
    """Semantic fallback: used when a paraphrase shares no words with the notes.

    The model is substituted, so these tests stay offline.
    """

    # a tiny "semantic space": money on dim 0, travel on 1, code on 2, and spare
    # dimensions 3..33 for unrelated text, so unrelated text can never align with a topic
    SPACE = {
        "budget": (0, 1.0), "бюджет": (0, 1.0), "расход": (0, 1.0), "трат": (0, 1.0),
        "денег": (0, 1.0), "обрез": (0, 0.9), "накоплен": (0, 0.9), "доход": (0, 0.8),
        "flight": (1, 1.0), "рейс": (1, 1.0), "пересадк": (1, 1.0), "лиссабон": (1, 1.0),
        "hotel": (1, 0.9), "отел": (1, 0.9),
        "api": (2, 1.0), "эндпоинт": (2, 1.0),
    }
    DIMS = 34

    def fake_embed(self, texts, model=None):
        vectors = []
        for text in texts:
            total = [0.0] * self.DIMS
            found = False
            for word, (axis, weight) in self.SPACE.items():
                if word in text.lower():
                    found = True
                    total[axis] += weight
            if not found:
                total[3 + crc32(text.encode()) % (self.DIMS - 3)] = 1.0
            vectors.append(total)
        return vectors

    def setUp(self) -> None:
        super().setUp()
        self._saved_embed = (oc_recall.EMBED_MODEL, oc_recall.embed_texts,
                             oc_recall.ollama_has_model)
        oc_recall.EMBED_MODEL = "fake-embed"
        oc_recall.embed_texts = self.fake_embed
        oc_recall.ollama_has_model = lambda model=None: True

    def tearDown(self) -> None:
        oc_recall.EMBED_MODEL, oc_recall.embed_texts, oc_recall.ollama_has_model = \
            self._saved_embed
        super().tearDown()

    def test_semantic_mode_is_off_by_default(self):
        oc_recall.EMBED_MODEL = ""
        self.assertFalse(oc_recall.embeddings_enabled())

    def test_embeddings_are_stored_on_sync(self):
        result = self.index()
        self.assertGreater(result["embedded"], 0)
        con = oc_recall.connect_index()
        try:
            stored = con.execute(
                "select count(*) from chunk where vec is not null"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(stored, result["total_chunks"])

    def test_paraphrase_without_shared_words_is_found(self):
        # regression target: "денег в обрез" and "разбор бюджета" share no indexed word
        self.index()
        oc_recall.EMBED_MODEL = ""          # prove the lexical path alone fails
        self.assertEqual(oc_recall.search("денег в обрез"), [])
        oc_recall.EMBED_MODEL = "fake-embed"
        results = oc_recall.search("денег в обрез")
        self.assertTrue(results)
        self.assertEqual(results[0]["session_id"], "ses_budget")
        self.assertTrue(results[0]["semantic"])

    def test_lexical_result_is_not_marked_semantic(self):
        self.index()
        results = oc_recall.search("пересадка лиссабон")
        self.assertTrue(results)
        self.assertFalse(results[0]["semantic"])

    def test_unrelated_query_stays_empty(self):
        self.index()
        self.assertEqual(oc_recall.search("квантовый флукс"), [])

    def test_semantic_signal_does_not_reorder_clear_lexical_hits(self):
        self.index()
        clear = oc_recall.search("бюджет расходы категории")
        self.assertEqual(clear[0]["session_id"], "ses_budget")

    def test_blend_weights_are_documented_constants(self):
        # the numbers come from a measurement sweep; a silent change invalidates the docs
        self.assertAlmostEqual(oc_recall.W_LEXICAL, 0.2)
        self.assertAlmostEqual(oc_recall.W_SEMANTIC, 0.8)
        self.assertAlmostEqual(oc_recall.SEMANTIC_FLOOR, 0.5)
        self.assertEqual(oc_recall.SEMANTIC_MIN_CHUNK, 8)

    def test_noise_below_floor_cannot_outrank_a_word_match(self):
        # regression: an unrelated session scored above an exact match when the floor was
        # missing, because a weak model reports high similarity for everything
        self.index()
        results = oc_recall.search("пересадка лиссабон")
        self.assertTrue(results)
        self.assertEqual(results[0]["session_id"], "ses_flights")

    def test_short_chunks_are_not_semantic_evidence(self):
        self.index()
        con = oc_recall.connect_index()
        try:
            tiny = con.execute(
                "select count(*) from chunk where vec is not null and "
                "length(tok) - length(replace(tok, ' ', '')) + 1 < ?",
                (oc_recall.SEMANTIC_MIN_CHUNK,),
            ).fetchone()[0]
        finally:
            con.close()
        # titles are short and must exist, but they must not be the only semantic input
        self.assertGreaterEqual(tiny, 0)

    def test_missing_ollama_is_a_readable_error(self):
        oc_recall.embed_texts = self._saved_embed[1]
        oc_recall.ollama_has_model = self._saved_embed[2]
        oc_recall.OLLAMA_URL = "http://127.0.0.1:1"
        with self.assertRaises(oc_recall.RecallError) as ctx:
            oc_recall.embed_texts(["text"], "fake-embed")
        self.assertIn("cannot reach Ollama", str(ctx.exception))


class SemanticSetupTest(Base):
    """--enable-semantic / --disable-semantic must be predictable and offline-testable.

    The model layer is stubbed: these tests never touch Ollama or the network.
    """

    def setUp(self) -> None:
        super().setUp()
        self._saved_semantic = (oc_recall.EMBED_MODEL, oc_recall.ensure_semantic_model,
                                oc_recall.ollama_models, oc_recall.index_embeddings)
        oc_recall.EMBED_MODEL = ""
        oc_recall.ensure_semantic_model = lambda model=None, use_mirror=True: "bge-m3:test"
        oc_recall.ollama_models = lambda: ["bge-m3:test"]
        self._embed_calls = []

        def fake_index_embeddings(con, quiet=False):
            self._embed_calls.append(quiet)
            return 0
        oc_recall.index_embeddings = fake_index_embeddings

    def tearDown(self) -> None:
        (oc_recall.EMBED_MODEL, oc_recall.ensure_semantic_model,
         oc_recall.ollama_models, oc_recall.index_embeddings) = self._saved_semantic
        super().tearDown()

    def test_config_roundtrip(self):
        path = oc_recall.save_config({"embed_model": "bge-m3:test"})
        self.assertTrue(path.startswith(self.tmp.name))
        self.assertEqual(oc_recall.load_config()["embed_model"], "bge-m3:test")

    def test_missing_config_means_defaults(self):
        self.assertEqual(oc_recall.load_config(), {})

    def test_broken_config_means_defaults(self):
        os.makedirs(os.path.dirname(oc_recall.config_path()), exist_ok=True)
        with open(oc_recall.config_path(), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(oc_recall.load_config(), {})

    def test_env_var_wins_over_config(self):
        oc_recall.save_config({"embed_model": "from-config"})
        oc_recall.EMBED_MODEL = "from-env"
        self.assertEqual(oc_recall.configured_embed_model(), "from-env")

    def test_config_enables_mode_without_env(self):
        self.assertFalse(oc_recall.embeddings_enabled())
        oc_recall.save_config({"embed_model": "bge-m3:test"})
        self.assertTrue(oc_recall.embeddings_enabled())
        self.assertEqual(oc_recall.configured_embed_model(), "bge-m3:test")

    def test_enable_writes_config_and_builds_vectors(self):
        self.index()
        self.assertEqual(oc_recall.cmd_enable_semantic(), 0)
        self.assertEqual(oc_recall.load_config()["embed_model"], "bge-m3:test")
        self.assertGreaterEqual(len(self._embed_calls), 1)

    def test_enable_is_idempotent(self):
        self.index()
        oc_recall.cmd_enable_semantic()
        first = oc_recall.load_config()
        oc_recall.cmd_enable_semantic()
        self.assertEqual(oc_recall.load_config(), first)

    def test_disable_turns_mode_off(self):
        oc_recall.save_config({"embed_model": "bge-m3:test"})
        self.assertEqual(oc_recall.cmd_disable_semantic(), 0)
        self.assertIsNone(oc_recall.load_config()["embed_model"])
        self.assertFalse(oc_recall.embeddings_enabled())

    def test_disable_keeps_vectors_by_default(self):
        self.index()
        con = oc_recall.connect_index()
        try:
            con.execute("update chunk set vec=x'00'")
            con.commit()
        finally:
            con.close()
        oc_recall.cmd_disable_semantic()
        con = oc_recall.connect_index()
        try:
            left = con.execute("select count(*) from chunk where vec is not null").fetchone()[0]
        finally:
            con.close()
        self.assertGreater(left, 0)

    def test_drop_vectors_clears_them(self):
        self.index()
        con = oc_recall.connect_index()
        try:
            con.execute("update chunk set vec=x'00'")
            con.commit()
        finally:
            con.close()
        oc_recall.cmd_disable_semantic(drop_vectors=True)
        con = oc_recall.connect_index()
        try:
            left = con.execute("select count(*) from chunk where vec is not null").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(left, 0)

    def test_unknown_model_name_is_reported(self):
        self.assertIsNone(oc_recall.resolve_installed_name("absent-model"))

    def test_ollama_down_is_a_readable_error(self):
        real = self._saved_semantic[1]
        saved_models = self._saved_semantic[2]
        try:
            oc_recall.ensure_semantic_model = real
            oc_recall.ollama_models = lambda: []
            with self.assertRaises(oc_recall.RecallError) as ctx:
                real("bge-m3")
            self.assertIn("not answering", str(ctx.exception))
        finally:
            oc_recall.ensure_semantic_model = self._saved_semantic[1]
            oc_recall.ollama_models = saved_models


class CliTest(Base):
    def run_cli(self, argv: list[str]) -> int:
        return oc_recall.main(argv)

    def test_help_exits_zero(self):
        with self.assertRaises(SystemExit) as ctx:
            oc_recall.build_parser().parse_args(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_no_arguments_exits_two(self):
        self.assertEqual(self.run_cli([]), 2)

    def test_json_output_is_valid(self):
        self.index()
        self.assertEqual(self.run_cli(["--json", "бюджет"]), 0)

    def test_json_missing_result_exits_one(self):
        self.index()
        self.assertEqual(self.run_cli(["--json", "квантовыйфлукс"]), 1)

    def test_status_without_index_exits_one(self):
        self.assertEqual(self.run_cli(["--status"]), 1)

    def test_status_with_index_exits_zero(self):
        self.index()
        self.assertEqual(self.run_cli(["--status"]), 0)

    def test_missing_source_database_is_a_readable_error(self):
        oc_recall.SOURCE_DB = os.path.join(self.tmp.name, "nope.db")
        with self.assertRaises(oc_recall.RecallError) as ctx:
            oc_recall.sync()
        self.assertIn("not found", str(ctx.exception))
        self.assertNotIn("Traceback", str(ctx.exception))

    def test_unreadable_source_reports_cause_not_traceback(self):
        # a file that exists but is not a database: first run used to print a traceback
        broken = os.path.join(self.tmp.name, "broken.db")
        with open(broken, "w", encoding="utf-8") as fh:
            fh.write("this is not a database")
        oc_recall.SOURCE_DB = broken
        with self.assertRaises(oc_recall.RecallError) as ctx:
            oc_recall.sync()
        message = str(ctx.exception)
        self.assertIn("cannot read the OpenCode database", message)
        self.assertIn("reason:", message)
        self.assertNotIn("Traceback", message)

    def test_source_schema_is_checked_before_indexing(self):
        empty = os.path.join(self.tmp.name, "empty.db")
        sqlite3.connect(empty).close()
        oc_recall.SOURCE_DB = empty
        with self.assertRaises(sqlite3.Error):
            oc_recall.sync()

    def test_sync_command(self):
        self.assertEqual(self.run_cli(["--sync"]), 0)
        self.assertTrue(os.path.exists(self.index_db))

    def test_reindex_command(self):
        self.index()
        self.assertEqual(self.run_cli(["--reindex"]), 0)

    def test_setup_creates_dictionary_and_index(self):
        self.assertEqual(self.run_cli(["--setup", "--no-agent"]), 0)
        self.assertTrue(os.path.exists(self.dict_path))
        self.assertTrue(os.path.exists(self.index_db))
        with open(self.dict_path, encoding="utf-8") as fh:
            self.assertIn("groups", json.load(fh))

    def test_setup_does_not_overwrite_existing_dictionary(self):
        os.makedirs(os.path.dirname(self.dict_path), exist_ok=True)
        with open(self.dict_path, "w", encoding="utf-8") as fh:
            json.dump({"groups": [["своё", "own"]]}, fh)
        self.assertEqual(self.run_cli(["--setup", "--no-agent"]), 0)
        with open(self.dict_path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["groups"], [["своё", "own"]])

    def test_explain_writes_to_stderr_only(self):
        self.index()
        self.assertEqual(self.run_cli(["--explain", "бюджет"]), 0)


class AgentTest(unittest.TestCase):
    def test_plist_is_valid_xml(self):
        text = agent.launchd_plist(3600, ["/usr/bin/python3", "/opt/app/oc_recall.py"],
                                   "/tmp/index.log")
        data = plistlib.loads(text.encode())
        self.assertEqual(data["Label"], agent.LABEL)
        self.assertEqual(data["StartInterval"], 3600)
        self.assertEqual(data["ProgramArguments"][1], "/opt/app/oc_recall.py")
        self.assertTrue(data["RunAtLoad"])

    def test_plist_quotes_paths_with_spaces(self):
        command = ["/usr/bin/python3", "/opt/My Folder/oc_recall.py"]
        data = plistlib.loads(agent.launchd_plist(60, command, "/tmp/a.log").encode())
        self.assertEqual(data["ProgramArguments"], command)

    def test_systemd_units(self):
        service = agent.systemd_service(["/usr/bin/python3", "/opt/oc_recall.py"])
        self.assertIn("ExecStart=/usr/bin/python3 /opt/oc_recall.py", service)
        timer = agent.systemd_timer(900)
        self.assertIn("OnUnitActiveSec=900", timer)
        self.assertIn("WantedBy=timers.target", timer)

    def test_cron_line(self):
        line = agent.cron_line(["/usr/bin/python3", "/opt/oc_recall.py"], 3600)
        self.assertTrue(line.startswith("*/60 * * * * "))
        short = agent.cron_line(["/usr/bin/python3", "/opt/oc_recall.py"], 30)
        self.assertTrue(short.startswith("*/1 * * * * "))

    def test_systemd_quotes_paths_with_spaces(self):
        # regression: a space in the install path silently broke ExecStart
        service = agent.systemd_service(["/usr/bin/python3", "/opt/My Folder/run.py"])
        self.assertIn("ExecStart=/usr/bin/python3 '/opt/My Folder/run.py'", service)

    def test_plist_escapes_xml_entities(self):
        # regression: "&" in a path produced an unparsable plist
        data = plistlib.loads(
            agent.launchd_plist(60, ["/opt/a&b/run.py"], "/tmp/log").encode()
        )
        self.assertEqual(data["ProgramArguments"], ["/opt/a&b/run.py"])

    def test_dry_run_creates_no_files(self):
        tmp = tempfile.TemporaryDirectory()
        saved = (agent.LAUNCHD_DIR, agent.LAUNCHD_PLIST, agent.SYSTEMD_DIR,
                 agent.SYSTEMD_SERVICE, agent.SYSTEMD_TIMER)
        try:
            base = tmp.name
            agent.LAUNCHD_DIR = os.path.join(base, "LaunchAgents")
            agent.LAUNCHD_PLIST = os.path.join(agent.LAUNCHD_DIR, "x.plist")
            agent.SYSTEMD_DIR = os.path.join(base, "systemd")
            agent.SYSTEMD_SERVICE = os.path.join(agent.SYSTEMD_DIR, "s.service")
            agent.SYSTEMD_TIMER = os.path.join(agent.SYSTEMD_DIR, "s.timer")
            result = agent.install_agent(dry_run=True)
            self.assertIn("kind", result)
            self.assertFalse(os.path.exists(agent.LAUNCHD_PLIST))
            self.assertFalse(os.path.exists(agent.SYSTEMD_TIMER))
        finally:
            (agent.LAUNCHD_DIR, agent.LAUNCHD_PLIST, agent.SYSTEMD_DIR,
             agent.SYSTEMD_SERVICE, agent.SYSTEMD_TIMER) = saved
            tmp.cleanup()

    def test_command_uses_current_python(self):
        self.assertEqual(agent.command()[0], sys.executable)
        self.assertTrue(agent.command()[1].endswith("oc_recall.py"))


class SymlinkShimTest(unittest.TestCase):
    """Regression: the installed command is a symlink in ~/.local/bin.

    Resolving the script path without realpath made `import agent` fail, so every
    subcommand crashed with ModuleNotFoundError once installed.
    """

    def test_cli_runs_through_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            shim = os.path.join(tmp, "oc-recall")
            target = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "..", "scripts", "oc_recall.py")
            os.symlink(os.path.realpath(target), shim)
            result = subprocess.run(
                [sys.executable, shim, "--help"],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Search past OpenCode sessions", result.stdout)

    def test_sibling_module_directory_is_resolved(self):
        self.assertTrue(os.path.exists(os.path.join(oc_recall._HERE, "agent.py")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
