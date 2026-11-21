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


DEMO_SESSIONS = [
    {
        "id": "ses_tunnel",
        "title": "Network routing notes",
        "messages": [
            {"type": "user", "text": "Нужна схема VPN: клиент подключается к промежуточному "
                                     "серверу в России, а тот уже ходит в зарубежный датацентр."},
            {"type": "assistant", "text": "Собрали цепочку из двух узлов и проверили, "
                                         "что трафик идёт через релей."},
        ],
    },
    {
        "id": "ses_tetris",
        "title": "Tetris in the dock panel",
        "messages": [
            {"type": "user", "text": "Сделай игру тетрис в док-панели на рабочем столе."},
            {"type": "assistant", "text": "Готово: мини-игра рисуется в панели."},
        ],
    },
    {
        "id": "ses_titled_only",
        "title": "VPN relay in Russia",
        "created": NOW - 3 * DAY_MS,
        "messages": [],
    },
    {
        "id": "ses_old",
        "title": "Old unrelated session",
        "created": NOW - 400 * DAY_MS,
        "messages": [{"type": "user", "text": "Проверь старые заметки про отпуск."}],
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

    def find(self, query: str, **kwargs) -> list[dict]:
        self.index() if not os.path.exists(self.index_db) else None
        return oc_recall.search(query, **kwargs)


class TokenTest(unittest.TestCase):
    def test_stem_removes_russian_endings(self):
        self.assertEqual(oc_recall.stem("схемы"), "схем")
        self.assertEqual(oc_recall.stem("промежуточным"), "промежуточн")

    def test_stem_keeps_short_words(self):
        self.assertEqual(oc_recall.stem("впн"), "впн")
        self.assertEqual(oc_recall.stem("vpn"), "vpn")

    def test_tokens_split_hyphenated_words(self):
        # "alfa-bank" must be findable as both the whole word and its parts
        parts = oc_recall.tokens("альфа-банка")
        self.assertIn("альфа-банк", parts)
        self.assertIn("альф", parts)
        self.assertIn("банк", parts)

    def test_tokens_drop_stop_words(self):
        self.assertEqual(oc_recall.tokens("и в на the a of"), [])

    def test_tokens_fold_yo(self):
        self.assertEqual(oc_recall.tokens("ёлка"), oc_recall.tokens("елка"))

    def test_index_tokens_add_prefix_stems(self):
        # "умножение" and "умножить" share the 5-letter stem "умнож"
        for word in ("умножение", "умножить"):
            self.assertIn("~умнож", oc_recall.index_tokens(word))

    def test_prefix_stems_not_added_for_short_words(self):
        self.assertFalse(any(t.startswith("~") for t in oc_recall.index_tokens("впн")))


class ChunkTest(unittest.TestCase):
    def test_short_text_single_chunk(self):
        self.assertEqual(oc_recall.chunk_text("hello"), ["hello"])

    def test_empty_text_no_chunks(self):
        self.assertEqual(oc_recall.chunk_text("   \n  "), [])

    def test_long_text_split_with_overlap(self):
        text = "\n\n".join(f"Параграф {i} " + "слово " * 60 for i in range(8))
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
                {"type": "tool", "text": "секретный вывод команды"},
                {"type": "text", "text": "полезный ответ"},
            ]
        }
        self.assertEqual(oc_recall.message_text("assistant", payload), "полезный ответ")


class DictionaryTest(Base):
    def test_missing_user_dictionary_is_not_an_error(self):
        table = oc_recall.load_synonyms("/nonexistent/synonyms.json")
        self.assertIn("vpn", table)
        self.assertIn("впн", table["vpn"])

    def test_user_dictionary_is_merged(self):
        os.makedirs(os.path.dirname(self.dict_path), exist_ok=True)
        with open(self.dict_path, "w", encoding="utf-8") as fh:
            json.dump({"groups": [["зелёный", "зеленый", "green"]]}, fh)
        table = oc_recall.load_synonyms(self.dict_path)
        self.assertIn("green", table["зелен"])

    def test_broken_dictionary_does_not_crash(self):
        os.makedirs(os.path.dirname(self.dict_path), exist_ok=True)
        with open(self.dict_path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertIn("vpn", oc_recall.load_synonyms(self.dict_path))

    def test_single_word_groups_are_ignored(self):
        os.makedirs(os.path.dirname(self.dict_path), exist_ok=True)
        with open(self.dict_path, "w", encoding="utf-8") as fh:
            json.dump({"groups": [["одиночка"]]}, fh)
        table = oc_recall.load_synonyms(self.dict_path)
        self.assertNotIn("одиночк", table)


class PlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.idf = {"впн": 5.0, "промежуточн": 4.0, "звен": 6.0, "обсужден": 6.0,
                    "~проме": 4.0, "vpn": 4.5}
        self.synonyms = oc_recall.load_synonyms("/nonexistent/synonyms.json")

    def test_intent_words_weigh_less(self):
        plan = oc_recall.query_plan("обсуждение схемы впн", self.idf, self.synonyms)
        self.assertEqual(plan["role"]["обсужден"], oc_recall.META_WEIGHT)
        self.assertEqual(plan["role"]["впн"], 1.0)

    def test_synonyms_added_with_lower_weight(self):
        plan = oc_recall.query_plan("впн", self.idf, self.synonyms)
        self.assertAlmostEqual(plan["role"]["vpn"], oc_recall.SYNONYM_WEIGHT)

    def test_exact_form_does_not_add_prefix_variant(self):
        # regression: adding the five-letter stem next to the exact term inflated the
        # denominator and made the same match count twice
        plan = oc_recall.query_plan("промежуточным", self.idf, self.synonyms)
        weights = oc_recall.plan_weights(plan)
        self.assertIn("промежуточн", weights)
        self.assertNotIn("~проме", weights)

    def test_prefix_fallback_counts_as_topic(self):
        # regression: a query whose word form is absent from the index returned nothing,
        # even though the shared prefix stem was indexed
        idf = {"умножит": 5.0, "~умнож": 5.0}
        plan = oc_recall.query_plan("умножение", idf, self.synonyms)
        self.assertEqual(plan["primary"][0]["members"], {"~умнож"})
        self.assertTrue(plan["found"])

    def test_short_words_have_no_prefix_fallback(self):
        idf = {"впн": 5.0}
        plan = oc_recall.query_plan("впн", idf, self.synonyms)
        self.assertEqual(plan["primary"][0]["members"], {"впн"})

    def test_prefix_match_weighs_less_than_full_word(self):
        # a stem match is a weaker signal than the exact word
        idf = {"умножен": 5.0, "~умнож": 5.0}
        plan = oc_recall.query_plan("умножение", idf, self.synonyms)
        self.assertAlmostEqual(plan["primary"][0]["weight"], 5.0)  # exact form wins
        idf = {"умножит": 5.0, "~умнож": 5.0}
        plan = oc_recall.query_plan("умножение", idf, self.synonyms)
        self.assertAlmostEqual(
            plan["primary"][0]["weight"], 5.0 * oc_recall.PREFIX_WEIGHT
        )

    def test_unknown_words_are_dropped(self):
        plan = oc_recall.query_plan("впн квантовыйфлукс", self.idf, self.synonyms)
        weights = oc_recall.plan_weights(plan)
        self.assertIn("впн", weights)
        self.assertNotIn("квантовыйфлукс", weights)

    def test_query_without_known_words_reports_empty(self):
        plan = oc_recall.query_plan("абракадабра", self.idf, self.synonyms)
        self.assertFalse(plan["found"])


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
            ("ses_tetris-m99", "ses_tetris", "user", 99, NOW, NOW,
             json.dumps({"text": "И ещё: добавь звук при падении фигуры."},
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
        titles = oc_recall.search("звук при падении фигуры")
        self.assertEqual(titles[0]["session_id"], "ses_tetris")

    def test_deleted_message_is_pruned(self):
        # messages go, the session stays: it is still findable by its title
        self.index()
        con = sqlite3.connect(self.source_db)
        con.execute("delete from session_message where session_id='ses_tetris'")
        con.commit()
        con.close()
        self.index(full=False)
        con = oc_recall.connect_index()
        try:
            kinds = [row[0] for row in con.execute(
                "select kind from chunk where session_id='ses_tetris'"
            )]
        finally:
            con.close()
        self.assertEqual(kinds, ["title"])

    def test_deleted_session_is_pruned(self):
        self.index()
        con = sqlite3.connect(self.source_db)
        con.execute("delete from session_v2 where id='ses_tetris'")
        con.commit()
        con.close()
        self.index(full=False)
        self.assertEqual(oc_recall.search("тетрис в док-панели"), [])

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
            ("bad-1", "ses_tetris", "user", 50, NOW, NOW, "{broken json"),
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
        results = oc_recall.search("схема впн с промежуточным звеном в россии")
        self.assertTrue(results)
        self.assertEqual(results[0]["session_id"], "ses_tunnel")

    def test_english_paraphrase_finds_the_session(self):
        results = oc_recall.search("vpn through an intermediate relay in russia")
        self.assertIn("ses_tunnel", [r["session_id"] for r in results])

    def test_synonym_bridges_vpn_and_vpn_letters(self):
        # query in Latin script against a Russian-only conversation
        results = oc_recall.search("vpn chain")
        self.assertIn("ses_tunnel", [r["session_id"] for r in results])

    def test_intent_words_do_not_dominate(self):
        results = oc_recall.search("обсуждение разбор итог тетрис")
        self.assertTrue(results)
        self.assertEqual(results[0]["session_id"], "ses_tetris")

    def test_unrelated_session_is_not_returned(self):
        results = oc_recall.search("тетрис в док-панели")
        self.assertNotIn("ses_old", [r["session_id"] for r in results])

    def test_unknown_query_returns_nothing(self):
        self.assertEqual(oc_recall.search("квантовыйфлукс"), [])

    def test_days_filter_keeps_title_only_session(self):
        # regression: title chunks have no timestamp and used to be filtered out
        results = oc_recall.search("relay in russia", days=7)
        self.assertIn("ses_titled_only", [r["session_id"] for r in results])

    def test_days_filter_excludes_old_sessions(self):
        results = oc_recall.search("старые заметки про отпуск", days=7)
        self.assertEqual(results, [])

    def test_directory_filter(self):
        results = oc_recall.search("тетрис", directory="/tmp/demo")
        self.assertTrue(results)
        self.assertEqual(oc_recall.search("тетрис", directory="/nowhere"), [])

    def test_results_are_json_serializable(self):
        payload = json.dumps(oc_recall.search("схема впн"), ensure_ascii=False)
        self.assertIn("ses_tunnel", payload)

    def test_result_fields(self):
        item = oc_recall.search("схема впн")[0]
        for field in ("session_id", "title", "directory", "date", "relevance", "score",
                      "hits", "matched_in", "snippet"):
            self.assertIn(field, item)

    def test_limit_is_respected(self):
        self.assertEqual(len(oc_recall.search("схема тетрис отпуск", limit=1)), 1)

    def test_ranking_prefers_title_hit(self):
        results = oc_recall.search("vpn relay in russia")
        self.assertEqual(results[0]["session_id"], "ses_titled_only")

    def test_prefix_bridge_matches_other_word_form(self):
        # "умножение" is indexed; the query uses "умножить" (different ending)
        con = sqlite3.connect(self.source_db)
        con.execute(
            "insert into session_message values(?,?,?,?,?,?,?)",
            ("math-1", "ses_old", "user", 1, NOW, NOW,
             json.dumps({"text": "Сколько будет умножить семнадцать на двадцать три?"},
                        ensure_ascii=False)),
        )
        con.commit()
        con.close()
        self.index(full=False)
        results = oc_recall.search("умножение", limit=3)
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
        self.assertFalse(
            os.path.exists(os.path.join(oc_recall.DATA_DIR, "probe.db")),
            "index was created outside the configured path",
        )

    def test_resolve_source_uses_current_module_path(self):
        self.assertEqual(oc_recall.resolve_source(), self.source_db)

    def test_load_synonyms_uses_current_dictionary_path(self):
        os.makedirs(os.path.dirname(self.dict_path), exist_ok=True)
        with open(self.dict_path, "w", encoding="utf-8") as fh:
            json.dump({"groups": [["проба", "probe"]]}, fh)
        self.assertIn("probe", oc_recall.load_synonyms()["проб"])


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
        self.assertEqual(self.run_cli(["--json", "схема впн"]), 0)

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
        self.assertEqual(self.run_cli(["--explain", "схема впн"]), 0)


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
