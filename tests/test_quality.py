#!/usr/bin/env python3
"""Ranking benchmark on a synthetic bilingual corpus: no network, no personal data.

Each case states a query the way a person would phrase it and the session that has to be
in the top five. Runs against a temporary database created here.

Run: python3 tests/test_quality.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)

import oc_recall  # noqa: E402
from test_oc_recall import build_source_db  # noqa: E402

NOW = int(time.time() * 1000)

CORPUS = [
    ("ses_vpn", "Network layout", "en-ru", [
        ("user", "Нужна схема VPN: клиент подключается к промежуточному серверу в России, "
                 "а тот уже ходит в зарубежный датацентр."),
        ("assistant", "Собрали цепочку из двух узлов: входной релей и выходной шлюз. "
                      "Проверили, что трафик идёт через оба."),
    ]),
    ("ses_tetris", "Tetris in the dock", "en", [
        ("user", "Build a small tetris game that renders inside the macOS dock panel."),
        ("assistant", "Done: the game draws into the dock and keeps score."),
    ]),
    ("ses_mail", "Mailbox setup", "en", [
        ("user", "Set up the work mailbox with Exchange ActiveSync and check the calendar."),
        ("assistant", "Mailbox connected, calendar events are readable."),
    ]),
    ("ses_epics", "Quarter epics", "en", [
        ("user", "Create the Q4 epics from the activity list and link them to business epics."),
        ("assistant", "Six epics created, each linked to a customer business epic."),
    ]),
    ("ses_router", "Router audit", "en", [
        ("user", "Read the router configuration and list the domain groups used for routing."),
        ("assistant", "Domain groups parsed, routing policies listed."),
    ]),
    ("ses_2fa", "Bank 2FA login", "en", [
        ("user", "The corporate VPN client asks for a one-time code on every login."),
        ("assistant", "Two-factor authentication is enforced by the gateway policy."),
    ]),
    ("ses_release", "Release calendar", "en", [
        ("user", "When is the release train cut and when does it roll out to clients?"),
        ("assistant", "The cut is on Tuesday, rollout starts the next working day."),
    ]),
    ("ses_disk", "External disk", "en", [
        ("user", "The laptop does not see the external SSD after unmounting."),
        ("assistant", "The disk sleeps and needs a power cycle before it appears again."),
    ]),
]

CASES = [
    # English query, Russian conversation: the synonym table has to bridge it
    ("vpn through an intermediate relay in russia", "ses_vpn"),
    # Russian query, English conversation
    ("маршрутизация через промежуточный узел", "ses_vpn"),
    # pure Russian paraphrase
    ("схема впн с промежуточным звеном", "ses_vpn"),
    ("игра тетрис в док панели мака", "ses_tetris"),
    ("почтовый ящик через exchange и календарь", "ses_mail"),
    ("создать эпики на квартал из списка активностей", "ses_epics"),
    ("аудит роутера и группы доменов", "ses_router"),
    ("роутер конфигурация домены", "ses_router"),
    ("код подтверждения при входе в корпоративный vpn", "ses_2fa"),
    ("two factor code on login", "ses_2fa"),
    ("когда отрезка релиза и раскатка на клиентов", "ses_release"),
    ("внешний диск не виден после отключения", "ses_disk"),
]


class QualityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = cls.tmp.name
        cls.source_db = os.path.join(root, "opencode.db")
        build_source_db(
            cls.source_db,
            [
                {
                    "id": sid,
                    "title": title,
                    "created": NOW - index * 86_400_000,
                    "messages": [{"type": t, "text": text} for t, text in messages],
                }
                for index, (sid, title, _lang, messages) in enumerate(CORPUS)
            ],
        )
        cls._saved = (oc_recall.INDEX_DB, oc_recall.SOURCE_DB, oc_recall.USER_DICT)
        oc_recall.INDEX_DB = os.path.join(root, "data", "index.db")
        oc_recall.SOURCE_DB = cls.source_db
        oc_recall.USER_DICT = os.path.join(root, "config", "synonyms.json")
        oc_recall.sync(full=True)

    @classmethod
    def tearDownClass(cls) -> None:
        (oc_recall.INDEX_DB, oc_recall.SOURCE_DB, oc_recall.USER_DICT) = cls._saved
        cls.tmp.cleanup()

    def test_hit_at_5_is_high(self):
        misses = []
        for query, expected in CASES:
            titles = [r["session_id"] for r in oc_recall.search(query, limit=5)]
            if expected not in titles:
                misses.append((query, expected, titles[:3]))
        hit_rate = (len(CASES) - len(misses)) / len(CASES)
        self.assertGreaterEqual(
            hit_rate, 0.9,
            "hit@5 dropped below 0.9; misses: "
            + json.dumps(misses, ensure_ascii=False, indent=1),
        )

    def test_expected_session_is_first_for_clear_queries(self):
        for query, expected in CASES[:4]:
            first = oc_recall.search(query, limit=1)
            self.assertTrue(first, f"no result for {query!r}")
            self.assertEqual(first[0]["session_id"], expected, f"wrong first result for {query!r}")

    def test_no_result_for_unrelated_query(self):
        self.assertEqual(oc_recall.search("квантовый флукс"), [])

    def test_both_languages_return_the_same_session(self):
        english = oc_recall.search("vpn through an intermediate relay", limit=3)
        russian = oc_recall.search("впн через промежуточное звено", limit=3)
        self.assertEqual(english[0]["session_id"], russian[0]["session_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
