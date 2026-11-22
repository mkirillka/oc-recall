#!/usr/bin/env python3
"""Ranking benchmark on a synthetic bilingual corpus: no network, no personal data.

Each case states a query the way a person would phrase it and the session that has to be
in the top five. Neutral domains: money, travel, programming. Runs against a temporary
database created here.

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
    ("ses_budget", "Quarterly budget", [
        ("user", "Разбери траты за квартал по категориям и посоветуй, какую долю дохода "
                 "откладывать в накопления."),
        ("assistant", "Расходы разложены по категориям: жильё, еда, транспорт. "
                      "Норма сбережений — 20% дохода."),
    ]),
    ("ses_loan", "Mortgage options", [
        ("user", "Сравни условия ипотеки в трёх банках и посчитай переплату за 10 лет."),
        ("assistant", "Переплата ниже у второго банка, но нужен первый взнос 20%."),
    ]),
    ("ses_flights", "Flights to Lisbon", [
        ("user", "Найди дешёвые билеты в Лиссабон с одной пересадкой на сентябрь."),
        ("assistant", "Стыковка в Стамбуле, 12 часов, цена на треть ниже прямого рейса."),
    ]),
    ("ses_hotel", "Hotel booking", [
        ("user", "Подбери отель в центре с бесплатной отменой и завтраком."),
        ("assistant", "Три варианта с бесплатной отменой до 48 часов, завтрак включён."),
    ]),
    ("ses_api", "API refactoring", [
        ("user", "Раздели эндпоинты интеграционного API по ресурсам и добавь тесты "
                 "на коды ошибок."),
        ("assistant", "Эндпоинты разделены, тесты покрывают 400 и 500, старые ручки "
                      "оставлены как deprecated."),
    ]),
    ("ses_db", "Database migration", [
        ("user", "Напиши миграцию для новой колонки и проверь, что она откатывается."),
        ("assistant", "Миграция добавлена, откат проверен на копии базы."),
    ]),
    ("ses_deploy", "Deploy pipeline", [
        ("user", "Собери пайплайн деплоя: тесты, сборка образа, выкатка на стейдж."),
        ("assistant", "Пайплайн из трёх шагов, выкатка только после зелёных тестов."),
    ]),
    ("ses_visa", "Visa paperwork", [
        ("user", "Какие документы нужны на визу и сколько ждать ответа?"),
        ("assistant", "Список из шести документов, срок рассмотрения — две недели."),
    ]),
]

CASES = [
    # English query, Russian conversation: the synonym table has to bridge it
    ("cheap flight with a layover in september", "ses_flights"),
    # Russian query, English title
    ("пересадка стамбул билеты", "ses_flights"),
    # pure Russian paraphrase
    ("сколько откладывать от дохода каждый месяц", "ses_budget"),
    ("категории расходов за квартал", "ses_budget"),
    ("переплата по ипотеке за десять лет", "ses_loan"),
    ("отель с отменой и завтраком", "ses_hotel"),
    ("hotel with free cancellation", "ses_hotel"),
    ("разделить ручки api и покрыть тестами", "ses_api"),
    ("миграция базы с откатом", "ses_db"),
    ("пайплайн сборки и выкатки", "ses_deploy"),
    ("документы на визу и сроки", "ses_visa"),
    ("visa documents processing time", "ses_visa"),
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
                for index, (sid, title, messages) in enumerate(CORPUS)
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
            self.assertEqual(first[0]["session_id"], expected,
                             f"wrong first result for {query!r}")

    def test_no_result_for_unrelated_query(self):
        self.assertEqual(oc_recall.search("квантовый флукс"), [])

    def test_both_languages_return_the_same_session(self):
        english = oc_recall.search("hotel with free cancellation", limit=3)
        russian = oc_recall.search("отель с бесплатной отменой", limit=3)
        self.assertEqual(english[0]["session_id"], russian[0]["session_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
