#!/usr/bin/env python3
"""Search past OpenCode sessions by meaning, not just by session title.

OpenCode's built-in session picker filters on the title only. This tool indexes the
actual conversation text into a separate SQLite file and answers queries like
"split the budget by category" with the sessions that discussed it.

Everything is local: no network, no models, no API keys. Only the Python standard
library is used (3.9+).

Index location:  $XDG_DATA_HOME/oc-recall/index.db      (default ~/.local/share/...)
User dictionary: $XDG_CONFIG_HOME/oc-recall/synonyms.json (default ~/.config/...)
OpenCode DB:     $OPENCODE_DB or $XDG_DATA_HOME/opencode/opencode.db
"""

from __future__ import annotations

import argparse
import array
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

APP = "oc-recall"

# The CLI is installed as a symlink in ~/.local/bin; resolve it so that sibling modules
# (agent.py) are importable from the real package directory.
_HERE = os.path.dirname(os.path.realpath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import agent  # noqa: E402  (needs the path fix above)

HOME = os.path.expanduser("~")
DATA_HOME = os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share")
CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")

DATA_DIR = os.path.join(DATA_HOME, APP)
CONFIG_DIR = os.path.join(CONFIG_HOME, APP)
INDEX_DB = os.environ.get("OC_RECALL_INDEX") or os.path.join(DATA_DIR, "index.db")
USER_DICT = os.environ.get("OC_RECALL_DICT") or os.path.join(CONFIG_DIR, "synonyms.json")
SOURCE_DB = os.environ.get("OPENCODE_DB") or os.path.join(DATA_HOME, "opencode", "opencode.db")

CHUNK_SIZE = 900
CHUNK_OVERLAP = 150
AUTO_SYNC_AFTER = int(os.environ.get("OC_RECALL_AUTO_SYNC", "300"))  # seconds
BUSY_TIMEOUT_MS = 5000

MIN_COVERAGE = 0.34     # share of the query weight a session must match
W_CHUNK = 0.8           # weight of the densest chunk (words close together)
W_SESSION = 0.2         # weight of the coverage across the whole session
KIND_BOOST = {"title": 1.9, "user": 1.1, "assistant": 1.0}

PREFIX_LEN = 5          # 5-letter stems bridge word forms: "mnozhenie"/"mnozhit"
PREFIX_WEIGHT = 0.8
META_WEIGHT = 0.25
SYNONYM_WEIGHT = 0.55

PLAN_LIMIT = 25

# Second search mode (semantic). Word matching is precise but blind to a paraphrase with
# no shared words, and completely blind across languages. A multilingual embedding model
# covers both gaps, so when it is enabled the final score blends the two signals:
#
#     score = W_LEX * min(word_cover, 1.5) + W_SEM * max(0, cos - FLOOR) / (1 - FLOOR)
#
# Measured on 24 real questions rewritten without their original words (see
# references/design.md): the blend keeps exact identifiers at rank 1, lifts same-language
# paraphrase from 8/24 to 13/24 at rank 1, and takes cross-language search from 3/24 to
# 24/24. Off unless OC_RECALL_EMBED_MODEL or ~/.config/oc-recall/config.json names a model;
# `--enable-semantic` writes that config for you.
EMBED_MODEL = os.environ.get("OC_RECALL_EMBED_MODEL", "").strip()
DEFAULT_SEMANTIC_MODEL = "bge-m3"
MIRROR_GGUF = ("https://hf-mirror.com/ggml-org/bge-m3-Q8_0-GGUF/resolve/main/"
               "bge-m3-q8_0.gguf")
OLLAMA_URL = os.environ.get("OC_RECALL_OLLAMA", "http://127.0.0.1:11434")
EMBED_BATCH = 16
EMBED_TIMEOUT = 120
W_LEXICAL = 0.2         # word-match share of the final score when semantic mode is on
W_SEMANTIC = 0.8        # meaning share of the final score when semantic mode is on
SEMANTIC_FLOOR = 0.5    # cosine below this counts as noise, whatever the model says
SEMANTIC_MIN_CHUNK = 8  # ignore very short chunks (titles) as semantic evidence

RU_STOP = frozenset(
    """и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
    только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если
    уже или ни быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей
    может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз
    тоже себе под будет ж тогда кто этот того потому этого какой совсем ним здесь этом
    один почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при наконец
    два об другой хоть после над больше тот через эти нас про всего них какая много
    разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой
    им более всегда конечно всю между это нужно надо очень тоже который которая которые
    также этот эта эти ему ее них нам вами нами
    the a an and or of to in on for with without is are was were be been being it this
    that these those as at by from into about over under again then than so if not no
    do does did can could should would will just also more most some any all
    through into onto from about above below after before between during while when
    where which who whom whose there their them they you your yours our ours its it's
    has have had having but both each other others only same such very well make made
    get got use used using one two three may must might shall should still even ever
    how what why does doing done let lets""".split()
)

ENDINGS = tuple(
    sorted(
        """ями иями ами ями ого его ому ему ыми ими ая яя ое ее ые ие ый ий ой ей ым им
        ах ях ов ев ию ии ия ам ям ом ем ью а я о е у ю ы и й ь""".split(),
        key=len,
        reverse=True,
    )
)

# Words that describe the act of asking ("discuss", "find", "plan") rather than the topic.
# Without damping they pull the ranking towards long sessions that contain a lot of text.
META_WORDS = frozenset(
    """обсужден обсуждат разговор вопрос поиск найти найт ищу ищем рассказ показ помоч
    хочу хотел хотелось нужн схем просмотр проверк разбор обзор итог отчет результат
    план вариант идея предложен сегодня вчера завтра недел месяц пожалуйст давай
    подскаж подсказ
    discuss discussed discussion find search looking looked show tell need want
    wanted please today yesterday tomorrow week month plan idea result summary
    overview review check""".split()
)

# Generic vocabulary only, bilingual on purpose: a query in one language has to find notes
# written in the other. Deliberately neutral domains (money, travel, programming) so the
# examples read as anyone's work. Names of your own machines, products and projects belong
# to the user dictionary (see DEFAULT_DICT), never to this file.
SYNONYM_GROUPS = (
    # money
    ("budget", "бюджет"),
    ("expense", "расход", "трат"),
    ("income", "доход", "зарплат"),
    ("savings", "накоплен", "откладыва"),
    ("loan", "кредит", "ипотек", "займ"),
    ("tax", "налог"),
    ("invoice", "счёт", "счет", "квитанц"),
    ("payment", "платёж", "платеж", "оплат"),
    ("price", "цен", "стоимост"),
    # travel
    ("flight", "рейс", "авиабилет", "билет", "перелёт", "перелет"),
    ("layover", "пересадк", "стыковк"),
    ("hotel", "отел", "гостиниц"),
    ("trip", "поездк", "путешеств", "отпуск"),
    ("booking", "бронирован", "бронь"),
    ("visa", "виз"),
    ("airport", "аэропорт"),
    ("train", "поезд"),
    ("route", "маршрут"),
    # programming
    ("function", "функци", "метод"),
    ("database", "база данных", "бд", "sql"),
    ("migration", "миграц"),
    ("endpoint", "эндпоинт", "ручк"),
    ("request", "запрос"),
    ("response", "ответ"),
    ("token", "токен"),
    ("cache", "кэш", "кеш"),
    ("queue", "очеред"),
    ("log", "лог", "журнал"),
    ("review", "ревью", "обзор"),
    ("refactor", "рефактор"),
    ("deploy", "деплой", "выкатк"),
    # general
    ("server", "сервер", "хост", "host", "vps"),
    ("mail", "почт", "email", "имейл", "письм", "ящик"),
    ("calendar", "календар", "встреч", "событ"),
    ("task", "задач", "тикет", "ticket", "issue"),
    ("release", "релиз", "верси", "раскатк"),
    ("model", "модел", "llm", "нейросет"),
    ("disk", "диск", "drive", "ssd", "hdd", "накопител"),
    ("network", "сеть", "вайфай", "wifi"),
    ("password", "парол", "passwd", "креды", "credential"),
    ("access", "доступ", "права", "permission"),
    ("terminal", "терминал", "консол", "console", "шелл", "shell", "zsh", "bash"),
    ("app", "приложен", "application", "программ", "софт"),
    ("screenshot", "скриншот", "снимок", "скрин"),
    ("audio", "звук", "озвучк", "tts", "голос"),
    ("system", "систем", "macos", "mac"),
    ("document", "документ", "doc", "docs"),
    ("error", "ошибк", "баг", "bug", "сбо", "паден", "fail"),
    ("install", "установ", "постав", "setup"),
    ("test", "проверк", "тест", "аудит", "audit"),
    ("script", "скрипт"),
    ("config", "конфиг", "configuration", "конфигурац"),
    ("update", "обновлен", "апдейт", "upgrade"),
)

DEFAULT_DICT = {
    "groups": [],
    "note": (
        "Add your own synonym groups here, e.g. "
        '{"groups": [["projectx", "px"], ["acme", "acme-corp"]]}. '
        "This file is personal: keep it out of git."
    ),
}


class RecallError(Exception):
    """A user-facing failure: printed as one line, no traceback."""


# --------------------------------------------------------------------- text


def stem(word: str) -> str:
    """Cut a Russian inflection off a word. Longest match wins, short stems are kept."""
    w = word.lower().replace("ё", "е")
    for ending in ENDINGS:
        if len(w) - len(ending) >= 4 and w.endswith(ending):
            return w[: -len(ending)]
    return w


def tokens(text: str) -> list[str]:
    """Lowercase, split, drop stop words, stem, and split hyphenated words."""
    lowered = text.lower().replace("ё", "е")
    out: list[str] = []
    for raw in re.findall(r"[\w\-.]+", lowered):
        parts = [raw] + (raw.split("-") if "-" in raw else [])
        for part in parts:
            part = part.strip(".")
            if len(part) < 2 or part in RU_STOP:
                continue
            out.append(stem(part))
    return out


def index_tokens(text: str) -> list[str]:
    """Tokens stored in the index: words plus 5-letter stems marked with '~'."""
    base = tokens(text)
    return base + ["~" + t[:PREFIX_LEN] for t in base if len(t) > PREFIX_LEN]


def load_synonyms(path: str | None = None) -> dict[str, frozenset[str]]:
    """Merge the built-in vocabulary with the user dictionary. Missing file is fine."""
    path = path or USER_DICT
    groups = list(SYNONYM_GROUPS)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        extra = data.get("groups") if isinstance(data, dict) else None
        if isinstance(extra, list):
            for group in extra:
                if isinstance(group, list) and len(group) > 1:
                    cleaned = tuple(stem(str(t).strip()) for t in group if str(t).strip())
                    if len(cleaned) > 1:
                        groups.append(cleaned)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        print(f"warning: cannot read {path}: {exc}", file=sys.stderr)

    table: dict[str, frozenset[str]] = {}
    for group in groups:
        # stem every alias: queries are matched on stems, so raw forms would never hit
        aliases = frozenset(stem(t) for t in group)
        for term in aliases:
            table[term] = table.get(term, frozenset()) | aliases
    return table


def query_plan(text: str, idf: dict[str, float], synonyms: dict[str, frozenset[str]]) -> dict:
    """Turn a query into weighted concepts.

    One concept = one query word plus its synonyms. A concept counts as covered when any
    of its forms is present, so an English query finds Russian notes about the same thing
    and word forms do not matter. Roles: topic (1.0), synonym (0.55), intent (0.25).
    Words absent from the index are dropped instead of diluting the coverage.
    """
    words = [t for t in dict.fromkeys(tokens(text)) if len(t) >= 3 or t.isdigit()]
    role: dict[str, float] = {}
    for word in words:
        role[word] = META_WEIGHT if word in META_WORDS else 1.0
    for word in list(role):
        if role[word] < 1.0:
            continue
        for alias in synonyms.get(word, ()):
            if alias and alias not in role and alias not in RU_STOP:
                role[alias] = SYNONYM_WEIGHT

    def forms(term: str) -> set[str]:
        """All index forms that should count for this term."""
        out = {term} if term in idf else set()
        if term not in idf and len(term) > PREFIX_LEN:
            prefixed = "~" + term[:PREFIX_LEN]
            if prefixed in idf:
                out.add(prefixed)
        return out

    concepts: list[dict] = []
    by_term: dict[str, dict] = {}
    for word in words:
        coef = role[word]
        members: set[str] = set()
        for candidate in [word, *synonyms.get(word, ())]:
            members |= forms(candidate)
        if not members:
            continue
        # one query word can be an alias of another; merge instead of double counting
        merged = None
        for concept in concepts:
            if concept["members"] & members:
                merged = concept
                break
        if merged is None:
            merged = {"members": set(), "coef": 0.0, "role": 0.0}
            concepts.append(merged)
        merged["members"] |= members
        merged["coef"] = max(merged["coef"], coef)
        merged["role"] = max(merged["role"], coef)

    for concept in concepts:
        best = 0.0
        for member in concept["members"]:
            weight = idf[member]
            # a match on the shared five-letter stem is a weaker signal than the full word
            if member.startswith("~"):
                weight *= PREFIX_WEIGHT
            best = max(best, weight)
        concept["weight"] = best * concept["coef"]
    concepts = [c for c in concepts if c["weight"] > 0]

    primary = [c for c in concepts if c["coef"] >= 1.0] or \
              [c for c in concepts if c["coef"] >= 0.5]
    denom = sum(c["weight"] for c in primary) or 1.0
    return {
        "role": role,
        "concepts": concepts,
        "primary": primary,
        "denom": denom,
        "found": bool(concepts),
    }


def plan_weights(plan: dict) -> dict[str, float]:
    """Flatten a plan into term -> weight, for --explain output."""
    return {
        member: concept["weight"]
        for concept in plan["concepts"]
        for member in concept["members"]
    }


def clean(text: str) -> str:
    return re.sub(r"[ \t]+\n", "\n", text.replace("\r\n", "\n")).strip()


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split a long message into overlapping chunks, preferring paragraph boundaries."""
    text = clean(text)
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            window = text[start:end]
            cut = max(
                window.rfind("\n\n"), window.rfind("\n"), window.rfind(". "),
                window.rfind("! "), window.rfind("? "),
            )
            if cut > size * 0.5:
                end = start + cut + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def message_text(msg_type: str, data: dict) -> str:
    """Extract the readable part of an OpenCode message."""
    if msg_type == "user":
        return clean(str(data.get("text") or ""))
    parts = [
        clean(str(p.get("text") or ""))
        for p in (data.get("content") or [])
        if isinstance(p, dict) and p.get("type") == "text"
    ]
    return clean("\n\n".join(p for p in parts if p))


def snippet(text: str, stems: set[str], width: int = 240) -> str:
    """Return a window of text around the first word that matched the query.

    Matches on the full stem and on the shared five-letter prefix, so a snippet is found
    even when the query used a different word form than the text.
    """
    lowered = text.lower().replace("ё", "е")
    prefixes = {s[:PREFIX_LEN] for s in stems if len(s) >= PREFIX_LEN}
    position = 0
    for match in re.finditer(r"[\w\-]+", lowered):
        candidate = stem(match.group())
        if candidate in stems or candidate[:PREFIX_LEN] in prefixes:
            position = match.start()
            break
    start = max(0, position - width // 3)
    fragment = re.sub(r"\s+", " ", text[start : start + width]).strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if start + width < len(text) else ""
    return prefix + fragment + suffix


# --------------------------------------------------------------------- index


SCHEMA = (
    "create table if not exists meta(key text primary key, value text)",
    """create table if not exists session(
        id text primary key, title text, directory text, project text,
        created integer, updated integer)""",
    """create table if not exists chunk(
        id integer primary key, session_id text not null, message_id text,
        kind text not null, ord integer not null default 0, ts integer,
        text text not null, tok text, vec blob)""",
    "create index if not exists chunk_session on chunk(session_id)",
    "create index if not exists chunk_message on chunk(message_id)",
    "create table if not exists df(term text primary key, n integer)",
)


def connect_index(path: str | None = None) -> sqlite3.Connection:
    """Open the index, creating the parent directory on first use."""
    path = path or INDEX_DB
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000)
    con.execute("pragma journal_mode=wal")
    con.execute("pragma synchronous=normal")
    con.execute(f"pragma busy_timeout={BUSY_TIMEOUT_MS}")
    return con


def init_schema(con: sqlite3.Connection) -> None:
    for statement in SCHEMA:
        con.execute(statement)
    # older indexes predate the optional embeddings column
    columns = {row[1] for row in con.execute("pragma table_info(chunk)")}
    if "vec" not in columns:
        con.execute("alter table chunk add column vec blob")
    con.commit()


def meta_get(con: sqlite3.Connection, key: str, default=None):
    try:
        row = con.execute("select value from meta where key=?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return default
    return row[0] if row else default


def meta_set(con: sqlite3.Connection, key: str, value) -> None:
    con.execute(
        "insert into meta(key, value) values(?,?) "
        "on conflict(key) do update set value=excluded.value",
        (key, str(value)),
    )
    con.commit()


def index_sessions(src: sqlite3.Connection, dst: sqlite3.Connection) -> int:
    """Refresh the session table and title chunks. Returns the number of titles changed."""
    rows = src.execute(
        "select s.id, s.title, s.directory, s.project_id, s.time_created, s.time_updated, "
        "p.name from session_v2 s left join project p on p.id = s.project_id"
    ).fetchall()
    dst.execute("delete from session")
    dst.executemany(
        "insert into session(id, title, directory, project, created, updated) "
        "values(?,?,?,?,?,?)",
        [(r[0], r[1], r[2], r[6] or "", r[4], r[5]) for r in rows],
    )
    stored = {
        sid: text
        for sid, text in dst.execute("select session_id, text from chunk where kind='title'")
    }
    changed = 0
    for sid, title in ((r[0], r[1]) for r in rows):
        title = clean(title or "")
        if stored.get(sid) == title:
            continue
        dst.execute("delete from chunk where session_id=? and kind='title'", (sid,))
        if title:
            dst.execute(
                "insert into chunk(session_id, message_id, kind, ord, ts, text, tok) "
                "values(?,NULL,'title',0,NULL,?,?)",
                (sid, title, " ".join(index_tokens(title))),
            )
            changed += 1
    return changed


def index_messages(src: sqlite3.Connection, dst: sqlite3.Connection, since_ms=None) -> tuple:
    """Index new or changed messages. Returns (messages, chunks, newest timestamp)."""
    query = (
        "select id, session_id, type, time_created, time_updated, data "
        "from session_message where type in ('user','assistant')"
    )
    if since_ms:
        rows = src.execute(query + " and time_updated >= ? order by time_updated", (since_ms,))
    else:
        rows = src.execute(query)

    pending: list[tuple[str, str, int, list[str]]] = []
    newest = 0
    unreadable = 0
    messages = 0
    for message_id, session_id, msg_type, created, updated, data in rows:
        newest = max(newest, updated or 0)
        try:
            payload = json.loads(data)
        except ValueError:
            unreadable += 1
            continue
        chunks = chunk_text(message_text(msg_type, payload))
        pending.append((message_id, session_id, created, chunks))
        if chunks:
            messages += 1

    dst.executemany("delete from chunk where message_id=?", [(p[0],) for p in pending])
    flat = [
        (session_id, message_id, created, ordn, chunk)
        for message_id, session_id, created, chunks in pending
        for ordn, chunk in enumerate(chunks)
    ]
    if flat:
        dst.executemany(
            "insert into chunk(session_id, message_id, kind, ord, ts, text, tok) "
            "values(?,?,'text',?,?,?,?)",
            [
                (session_id, message_id, ordn, created, chunk, " ".join(index_tokens(chunk)))
                for session_id, message_id, created, ordn, chunk in flat
            ],
        )
    dst.commit()
    return messages, len(flat), newest, unreadable


def rebuild_df(dst: sqlite3.Connection) -> int:
    counts: dict[str, int] = {}
    for (tok,) in dst.execute("select tok from chunk where tok is not null"):
        for term in set(tok.split()):
            counts[term] = counts.get(term, 0) + 1
    dst.execute("delete from df")
    dst.executemany("insert into df(term, n) values(?,?)", counts.items())
    dst.commit()
    return len(counts)


def prune(src: sqlite3.Connection, dst: sqlite3.Connection) -> int:
    """Drop chunks of sessions and messages that no longer exist in OpenCode."""
    live_sessions = {row[0] for row in dst.execute("select id from session")}
    stale_sessions = [
        row[0]
        for row in dst.execute("select distinct session_id from chunk")
        if row[0] not in live_sessions
    ]
    if stale_sessions:
        dst.executemany(
            "delete from chunk where session_id=?", [(s,) for s in stale_sessions]
        )
    live_messages = {row[0] for row in src.execute("select id from session_message")}
    stale_messages = [
        row[0]
        for row in dst.execute(
            "select distinct message_id from chunk where message_id is not null"
        )
        if row[0] not in live_messages
    ]
    if stale_messages:
        dst.executemany(
            "delete from chunk where message_id=?", [(m,) for m in stale_messages]
        )
    dst.commit()
    return len(stale_sessions) + len(stale_messages)


def resolve_source(path: str | None = None) -> str:
    """Return the OpenCode database path or raise a readable error."""
    path = path or SOURCE_DB
    if not os.path.exists(path):
        raise RecallError(
            f"OpenCode database not found: {path}\n"
            "Start OpenCode once, or point OPENCODE_DB at the real file."
        )
    return path


def open_source(path: str | None = None) -> sqlite3.Connection:
    """Open the OpenCode database read-only, turning SQLite failures into advice.

    A raw traceback here is useless to the user: the usual causes are a path that is not
    the database, a file being rewritten by OpenCode at that moment, or missing read
    permission. Each gets its own sentence.
    """
    path = resolve_source(path)
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_MS / 1000)
        # a cheap read proves the file is a usable database, not just an existing path
        con.execute("select count(*) from sqlite_master").fetchone()
        return con
    except sqlite3.DatabaseError as exc:
        raise RecallError(
            f"cannot read the OpenCode database: {path}\n"
            f"reason: {exc}\n"
            "Check that the file is readable and is not being rewritten right now; "
            "retry in a moment, or point OPENCODE_DB at the right file."
        ) from exc


def sync(full: bool = False, embed: bool = True, quiet: bool = True) -> dict:
    """Bring the index up to date. Incremental unless full=True.

    ``embed=False`` skips vector building, so a caller can show its own progress for the
    long step instead of letting it run silently inside the sync.
    """
    source_path = resolve_source()
    src = open_source(source_path)
    try:
        dst = connect_index()
        init_schema(dst)
        previous_source = meta_get(dst, "source_db")
        if previous_source and previous_source != source_path:
            full = True
        since = None
        if not full:
            last = meta_get(dst, "last_updated_ms")
            if last:
                since = max(int(last) - 1000, 0)
        if full:
            dst.execute("delete from chunk")
            dst.commit()

        titles = index_sessions(src, dst)
        messages, chunks, newest, unreadable = index_messages(src, dst, since)
        pruned = prune(src, dst)
        terms = rebuild_df(dst)
        embedded = index_embeddings(dst, quiet=quiet) if embed else 0

        meta_set(dst, "last_updated_ms", max(int(meta_get(dst, "last_updated_ms", 0) or 0), newest))
        meta_set(dst, "source_db", source_path)
        meta_set(dst, "synced_at", int(time.time()))

        total = dst.execute("select count(*) from chunk").fetchone()[0]
        sessions = dst.execute("select count(*) from session").fetchone()[0]
        dst.close()
        return {
            "sessions": sessions, "titles": titles, "messages": messages,
            "chunks": chunks, "pruned": pruned, "terms": terms, "total_chunks": total,
            "unreadable": unreadable, "embedded": embedded,
        }
    finally:
        src.close()


def maybe_autosync() -> None:
    """Refresh the index in the background of a search if it looks stale."""
    if not os.path.exists(INDEX_DB):
        return
    try:
        con = connect_index()
        last = int(meta_get(con, "synced_at", 0) or 0)
        con.close()
    except sqlite3.Error:
        return
    if time.time() - last > AUTO_SYNC_AFTER:
        try:
            sync()
        except (RecallError, sqlite3.Error):
            pass


# --------------------------------------------------------------------- search


def idf_map(con: sqlite3.Connection) -> dict[str, float]:
    total = con.execute("select count(*) from chunk").fetchone()[0] or 1
    return {
        term: math.log(1 + total / max(count, 1))
        for term, count in con.execute("select term, n from df")
    }


# --------------------------------------------------------------------- embeddings


def config_path() -> str:
    """Settings file location, resolved at call time so CONFIG_DIR overrides apply."""
    return os.environ.get("OC_RECALL_CONFIG") or os.path.join(CONFIG_DIR, "config.json")


def load_config(path: str | None = None) -> dict:
    """Read the small JSON settings file. A missing or broken file means defaults."""
    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(patch: dict, path: str | None = None) -> str:
    """Merge a patch into the settings file, writing atomically."""
    path = path or config_path()
    data = load_config(path)
    data.update(patch)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)
    return path


def configured_embed_model() -> str:
    """Model used for semantic mode: environment first, then the settings file."""
    if EMBED_MODEL:
        return EMBED_MODEL
    return str(load_config().get("embed_model") or "")


def embeddings_enabled() -> bool:
    """Semantic fallback is used only when a model is explicitly configured."""
    return bool(configured_embed_model())


def ollama_has_model(model: str | None = None) -> bool:
    """Check the local Ollama for the configured model. Network-free when unavailable."""
    model = model or configured_embed_model()
    if not model:
        return False
    try:
        request = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(request, timeout=5) as response:
            names = [item.get("name", "") for item in json.load(response).get("models", [])]
    except (urllib.error.URLError, ValueError, OSError):
        return False
    short = model.split(":")[0]
    return any(name == model or name.split(":")[0] == short for name in names)


def embed_texts(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Embed texts locally. Kept a plain function so tests can substitute it."""
    model = model or configured_embed_model()
    if not model:
        raise RecallError(
            "semantic mode needs a model: run `oc-recall --enable-semantic`, or set "
            "OC_RECALL_EMBED_MODEL to a model installed in Ollama"
        )
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch = texts[start : start + EMBED_BATCH]
        payload = json.dumps({"model": model, "input": batch}).encode()
        request = urllib.request.Request(
            f"{OLLAMA_URL}/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=EMBED_TIMEOUT) as response:
                vectors.extend(json.load(response)["embeddings"])
        except (urllib.error.URLError, OSError) as exc:
            raise RecallError(
                f"cannot reach Ollama at {OLLAMA_URL}: {exc}\n"
                "Start it with `ollama serve`, or run --disable-semantic to use "
                "word search only."
            ) from exc
        except (KeyError, ValueError) as exc:
            raise RecallError(f"unexpected Ollama response: {exc}") from exc
    return vectors


def pack_vec(values: list[float]) -> bytes:
    """Normalize and pack a vector so a dot product equals cosine similarity."""
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return array.array("f", [v / norm for v in values]).tobytes()


def unpack_vec(blob: bytes) -> array.array:
    values = array.array("f")
    values.frombytes(blob)
    return values


def cosine(left, right) -> float:
    """Vectors are normalized when packed, so a dot product is the cosine."""
    return sum(a * b for a, b in zip(left, right))


def index_embeddings(con: sqlite3.Connection, quiet: bool = False) -> int:
    """Embed chunks that have no vector yet. Returns how many were added."""
    if not embeddings_enabled() or not ollama_has_model():
        return 0
    rows = con.execute(
        "select id, text from chunk where vec is null order by id"
    ).fetchall()
    if not rows:
        return 0
    added = 0
    for start in range(0, len(rows), EMBED_BATCH):
        batch = rows[start : start + EMBED_BATCH]
        vectors = embed_texts([text for _id, text in batch])
        con.executemany(
            "update chunk set vec=? where id=?",
            [(pack_vec(vector), chunk_id) for (chunk_id, _text), vector in zip(batch, vectors)],
        )
        con.commit()   # keep progress if the run is interrupted
        added += len(batch)
        if not quiet:
            print(f"  embedded {added}/{len(rows)}", file=sys.stderr, flush=True)
    con.commit()
    return added


def drop_embeddings(con: sqlite3.Connection) -> int:
    """Remove stored vectors, keeping the lexical index intact."""
    con.execute("update chunk set vec = null")
    con.commit()
    return con.total_changes


# ---------------------------------------------------------------- semantic setup


def ollama_models() -> list[str]:
    """Names of the models installed in the local Ollama, empty when it is down."""
    try:
        request = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(request, timeout=5) as response:
            return [item.get("name", "") for item in json.load(response).get("models", [])]
    except (urllib.error.URLError, ValueError, OSError):
        return []


def resolve_installed_name(short: str) -> str | None:
    """Return the exact tag Ollama uses for a model, or None when it is absent."""
    for name in ollama_models():
        if name == short or name.split(":")[0] == short.split(":")[0]:
            return name
    return None


def pull_model(model: str) -> bool:
    """Ask Ollama to pull a model. False when the registry is unreachable."""
    binary = shutil.which("ollama")
    if not binary:
        return False
    try:
        result = subprocess.run(
            [binary, "pull", model], capture_output=True, text=True, timeout=1800
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def download_gguf(url: str, target: str) -> int:
    """Download a GGUF file with progress on stderr. Returns the byte count."""
    request = urllib.request.Request(url, headers={"User-Agent": f"{APP}/1"})
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response, \
                open(target, "wb") as handle:
            expected = int(response.headers.get("Content-Length") or 0)
            while True:
                block = response.read(1024 * 256)
                if not block:
                    break
                handle.write(block)
                total += len(block)
                if expected:
                    print(f"\r  downloaded {total / 1e6:.0f}/{expected / 1e6:.0f} MB",
                          end="", file=sys.stderr, flush=True)
        print("", file=sys.stderr)
    except (urllib.error.URLError, OSError) as exc:
        raise RecallError(
            f"cannot download the model file: {exc}\n"
            "Retry later, or pull the model manually and run --enable-semantic again."
        ) from exc
    return total


def create_model_from_gguf(gguf_path: str, name: str) -> bool:
    """Register a local GGUF file with Ollama as a model."""
    binary = shutil.which("ollama")
    if not binary:
        return False
    with tempfile.TemporaryDirectory() as workdir:
        modelfile = os.path.join(workdir, "Modelfile")
        with open(modelfile, "w", encoding="utf-8") as handle:
            handle.write(f"FROM {gguf_path}\n")
        try:
            result = subprocess.run(
                [binary, "create", name, "-f", modelfile],
                capture_output=True, text=True, timeout=900,
            )
        except (OSError, subprocess.SubprocessError):
            return False
    return result.returncode == 0


def ensure_semantic_model(model: str | None = None, use_mirror: bool = True) -> str:
    """Return an installed model name, fetching the model when needed.

    Order: already installed, then `ollama pull`, then the Hugging Face mirror. The
    mirror exists because some networks block the Ollama registry at the TLS handshake
    while the mirror stays reachable.
    """
    if not ollama_models():
        raise RecallError(
            f"Ollama is not answering at {OLLAMA_URL}\n"
            "Start it with `ollama serve` and retry."
        )
    short = model or DEFAULT_SEMANTIC_MODEL

    installed = resolve_installed_name(short)
    if installed:
        print(f"model:      {installed} (already installed)", flush=True)
        return installed

    print(f"model:      {short} not installed, asking Ollama to pull it", flush=True)
    if pull_model(short):
        name = resolve_installed_name(short) or short
        print(f"model:      {name} pulled", flush=True)
        return name

    if not use_mirror:
        raise RecallError(
            f"cannot pull {short} and the mirror is disabled.\n"
            "Install the model manually, then run --enable-semantic again."
        )

    print("model:      registry unreachable, downloading from the Hugging Face mirror",
          flush=True)
    with tempfile.TemporaryDirectory() as workdir:
        gguf = os.path.join(workdir, "model.gguf")
        size = download_gguf(MIRROR_GGUF, gguf)
        print(f"model:      downloaded {size / 1e6:.0f} MB, registering with Ollama", flush=True)
        created_name = f"{short.split(':')[0]}:mirror"
        if not create_model_from_gguf(gguf, created_name):
            raise RecallError(
                "cannot register the downloaded model with Ollama.\n"
                "Check `ollama list` and the free disk space, then retry."
            )
    name = resolve_installed_name(created_name) or created_name
    print(f"model:      {name} created from the mirror", flush=True)
    return name


def search(
    query: str,
    limit: int = 5,
    directory: str | None = None,
    days: int | None = None,
    synonyms: dict[str, frozenset[str]] | None = None,
) -> list[dict]:
    """Return the best matching sessions, most relevant first."""
    if not os.path.exists(INDEX_DB):
        raise RecallError(_empty_index_message())
    synonyms = synonyms if synonyms is not None else load_synonyms()

    con = connect_index()
    try:
        init_schema(con)
        idf = idf_map(con)
        if not idf:
            raise RecallError(_empty_index_message())
        plan = query_plan(query, idf, synonyms)

        query_vector = None
        if embeddings_enabled():
            has_vectors = con.execute(
                "select 1 from chunk where vec is not null limit 1"
            ).fetchone()
            if has_vectors and ollama_has_model():
                query_vector = unpack_vec(pack_vec(embed_texts([query])[0]))

        if not plan["found"] and query_vector is None:
            return []
        primary = plan["primary"]
        denom = plan["denom"] or 1.0

        sql = (
            "select c.session_id, c.kind, c.text, c.tok, "
            "coalesce(c.ts, s.created) as stamp, s.title, s.directory, s.created, c.vec "
            "from chunk c join session s on s.id = c.session_id where c.tok is not null"
        )
        params: list = []
        if directory:
            sql += " and s.directory like ?"
            params.append(f"%{directory}%")
        if days:
            sql += " and coalesce(c.ts, s.created) >= ?"
            params.append(int((time.time() - days * 86400) * 1000))

        sessions: dict[str, dict] = {}
        for row in con.execute(sql, params):
            session_id, kind, text, tok, stamp, title, path, created, vec = row
            words = set(tok.split())
            covered = [c for c in primary if c["members"] & words]
            covered_ids = {id(c) for c in covered}
            chunk_cover = sum(c["weight"] for c in covered) / denom if covered else 0.0
            # synonyms of the covered concepts also count, at their own weight
            side = sum(
                c["weight"] for c in plan["concepts"]
                if id(c) not in covered_ids and c["members"] & words
            ) / denom
            local = chunk_cover * KIND_BOOST.get(kind, 1.0) + 0.35 * side

            similarity = 0.0
            if query_vector is not None and vec and len(words) >= SEMANTIC_MIN_CHUNK:
                similarity = max(cosine(query_vector, unpack_vec(vec)), 0.0)
                local += 0.3 * similarity * KIND_BOOST.get(kind, 1.0)

            if not covered and similarity < SEMANTIC_FLOOR:
                continue

            record = sessions.get(session_id)
            if record is None:
                sessions[session_id] = {
                    "session_id": session_id, "title": title, "directory": path,
                    "created": created, "stamp": stamp, "hits": 1,
                    "covered": covered_ids,
                    "chunk_cover": chunk_cover, "local": local,
                    "similarity": similarity, "excerpt": text, "kind": kind,
                }
            else:
                record["hits"] += 1
                record["covered"].update(covered_ids)
                record["chunk_cover"] = max(record["chunk_cover"], chunk_cover)
                record["similarity"] = max(record["similarity"], similarity)
                if local > record["local"]:
                    record["local"] = local
                    record["excerpt"] = text
                    record["kind"] = kind
                    record["stamp"] = stamp
    finally:
        con.close()

    results = []
    for record in sessions.values():
        # coverage across the whole session: a topic may be spread over several messages
        session_cover = sum(
            c["weight"] for c in primary if id(c) in record["covered"]
        ) / denom
        cover = W_CHUNK * record["chunk_cover"] + W_SESSION * session_cover

        if query_vector is None:
            if cover < MIN_COVERAGE:
                continue
            record["score"] = cover * KIND_BOOST.get(record["kind"], 1.0) + record["local"] * 0.5
            record["semantic"] = False
        else:
            # meaning below the floor counts as zero, so noise cannot outrank a word match
            meaning = max(record["similarity"] - SEMANTIC_FLOOR, 0.0) / (1 - SEMANTIC_FLOOR)
            if cover < MIN_COVERAGE and meaning <= 0.0:
                continue
            record["score"] = (
                W_LEXICAL * min(cover, 1.5)
                + W_SEMANTIC * meaning
                + 0.15 * record["local"] / KIND_BOOST.get(record["kind"], 1.0)
            )
            record["semantic"] = cover < MIN_COVERAGE
        record["cover"] = cover
        results.append(record)
    results.sort(key=lambda r: r["score"], reverse=True)
    results = results[: max(PLAN_LIMIT, limit)]

    stems = set()
    for concept in plan["concepts"]:
        if concept["coef"] >= 0.5:
            stems |= concept["members"]
    output = []
    for record in results[:limit]:
        stamp = record.get("stamp") or record["created"]
        output.append(
            {
                "session_id": record["session_id"],
                "title": record["title"],
                "directory": record["directory"],
                "date": time.strftime("%Y-%m-%d", time.localtime(stamp / 1000)),
                "relevance": round(record["cover"], 2),
                "score": round(record["score"], 2),
                "hits": record["hits"],
                "matched_in": record["kind"],
                "semantic": record.get("semantic", False),
                "snippet": snippet(record["excerpt"] or "", stems),
            }
        )
    return output


# --------------------------------------------------------------------- setup


def _empty_index_message() -> str:
    return (
        f"Index is empty or missing: {INDEX_DB}\n"
        f"Run: {sys.argv[0]} --setup"
    )


def ensure_config() -> tuple[str, bool]:
    """Create the user dictionary template if it does not exist yet."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    created = False
    if not os.path.exists(USER_DICT):
        with open(USER_DICT, "w", encoding="utf-8") as fh:
            json.dump(DEFAULT_DICT, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        try:
            os.chmod(USER_DICT, 0o600)
        except OSError:
            pass
        created = True
    return USER_DICT, created


def cmd_setup(install_agent: bool = True, interval: int = 3600) -> int:
    # validate the environment first: a failure must not look like a half-done setup
    resolve_source()
    os.makedirs(DATA_DIR, exist_ok=True)
    config_path, created = ensure_config()
    print(f"data dir:   {DATA_DIR}")
    print(f"index:      {INDEX_DB}")
    print(f"dictionary: {config_path}{' (created)' if created else ''}")

    if embeddings_enabled() and ollama_has_model():
        print(f"semantic:   on via {configured_embed_model()}, embedding new chunks "
              "(progress on stderr)", flush=True)
    started = time.time()
    result = sync(full=not os.path.exists(INDEX_DB), quiet=False)
    print(
        f"indexed:    {result['sessions']} sessions, {result['total_chunks']} chunks, "
        f"{result['terms']} terms ({time.time() - started:.1f}s)", flush=True
    )
    if result["unreadable"]:
        print(f"warning:    {result['unreadable']} unreadable messages skipped")

    if install_agent:
        status = agent.install_agent(interval=interval)
        print(f"updater:    {status['message']}")
    else:
        print("updater:    not installed (run --install-agent to add it)")
    print("")
    print(f"Try:  {sys.argv[0]} \"savings rate for the year\"")
    return 0


def cmd_enable_semantic(model: str | None = None, use_mirror: bool = True) -> int:
    """Turn on the second search mode: ensure the model, save config, build vectors."""
    name = ensure_semantic_model(model, use_mirror=use_mirror)
    path = save_config({"embed_model": name})
    print(f"config:     {path} (embed_model = {name})", flush=True)

    started = time.time()
    result = sync(full=False, embed=False)
    print(f"index:      {result['sessions']} sessions, {result['total_chunks']} chunks",
          flush=True)
    con = connect_index()
    try:
        pending = con.execute("select count(*) from chunk where vec is null").fetchone()[0]
        if pending:
            print(f"vectors:    embedding {pending} chunks, this takes a few minutes",
                  flush=True)
        embedded = index_embeddings(con, quiet=False)
        pending = con.execute("select count(*) from chunk where vec is null").fetchone()[0]
    finally:
        con.close()
    print(f"vectors:    {embedded} embedded, {pending} still without a vector, "
          f"{time.time() - started:.0f}s", flush=True)
    if pending:
        print("note:       run --reindex to embed the rest")
    print("")
    print("Semantic mode is on. Both signals are blended, words pinning exact matches,",
          "meaning covering paraphrases and other languages.", flush=True)
    print("Hits found only by meaning are flagged `semantic` in --json output.", flush=True)
    print("Turn it off with --disable-semantic.", flush=True)
    return 0


def cmd_disable_semantic(drop_vectors: bool = False) -> int:
    """Turn the second search mode off, keeping the lexical index."""
    path = save_config({"embed_model": None})
    print(f"config:     {path} (embed_model = off)", flush=True)
    if drop_vectors:
        con = connect_index()
        try:
            init_schema(con)
            con.execute("update chunk set vec = null")
            con.commit()
        finally:
            con.close()
        print("vectors:    removed")
    else:
        print("vectors:    kept, so re-enabling needs no reindex")
    return 0


def cmd_status() -> int:
    print(f"index:      {INDEX_DB}")
    if not os.path.exists(INDEX_DB):
        print("state:      missing — run --setup")
        return 1
    con = connect_index()
    try:
        init_schema(con)
        sessions = con.execute("select count(*) from session").fetchone()[0]
        chunks = con.execute("select count(*) from chunk").fetchone()[0]
        terms = con.execute("select count(*) from df").fetchone()[0]
        synced = int(meta_get(con, "synced_at", 0) or 0)
        source = meta_get(con, "source_db") or "(unknown)"
    finally:
        con.close()
    size = os.path.getsize(INDEX_DB) / 1e6
    print(f"source:     {source}")
    print(f"sessions:   {sessions}")
    print(f"chunks:     {chunks}")
    print(f"terms:      {terms}")
    print(f"size:       {size:.1f} MB")
    if synced:
        age = int(time.time() - synced)
        print(f"synced:     {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(synced))} "
              f"({age // 60} min ago)")
    else:
        print("synced:     never")
    print(f"dictionary: {USER_DICT}"
          f"{'' if os.path.exists(USER_DICT) else ' (missing — created on next --setup)'}")

    status = agent.agent_status()
    print(f"updater:    {status['message']}")

    con = connect_index()
    try:
        embedded = con.execute("select count(*) from chunk where vec is not null").fetchone()[0]
    finally:
        con.close()
    if not embeddings_enabled():
        print("semantic:   off (run --enable-semantic to add the second search mode)")
    elif embedded:
        print(f"semantic:   on, {embedded} chunks embedded via {configured_embed_model()}")
    else:
        print(f"semantic:   on but nothing embedded yet — run --reindex")

    last_sync = synced
    if last_sync:
        changed = newest_changed_since(last_sync)
        if changed:
            print(f"note:       {changed} messages changed since the last sync — run --sync")
    return 0


def newest_changed_since(timestamp: int) -> int:
    """Count messages newer than the index timestamp; used only for the status hint."""
    try:
        src = open_source()
    except (RecallError, sqlite3.Error):
        return 0
    try:
        row = src.execute(
            "select count(*) from session_message where time_updated > ?",
            (timestamp * 1000,),
        ).fetchone()
        return row[0] if row else 0
    except sqlite3.Error:
        return 0
    finally:
        src.close()


def cmd_sync(full: bool = False) -> int:
    started = time.time()
    result = sync(full=full)
    print(
        f"indexed: {result['sessions']} sessions, {result['total_chunks']} chunks "
        f"(+{result['chunks']} new, -{result['pruned']} removed), "
        f"{result['terms']} terms ({time.time() - started:.1f}s)"
    )
    return 0


# --------------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP,
        description="Search past OpenCode sessions by meaning, not just by title.",
        epilog=(
            "Examples:\n"
            '  oc-recall "cheap flight with a layover"\n'
            '  oc-recall -n 10 --json "разбор бюджета по категориям"\n'
            '  oc-recall -d ~/src/project --days 30 "refactor the api layer"\n'
            "  oc-recall --setup\n"
            "  oc-recall --status"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("query", nargs="*", help="words to search for")
    parser.add_argument("-n", "--limit", type=int, default=5, help="results (default: 5)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--open", action="store_true", help="open the best session in OpenCode")
    parser.add_argument("-d", "--dir", help="only sessions whose path contains this text")
    parser.add_argument("--days", type=int, help="only the last N days")
    parser.add_argument("--explain", action="store_true", help="show how the query was parsed")
    parser.add_argument("--setup", action="store_true", help="create dirs, dictionary, index, updater")
    parser.add_argument("--status", action="store_true", help="show index state")
    parser.add_argument("--sync", action="store_true", help="bring the index up to date")
    parser.add_argument("--reindex", action="store_true", help="rebuild the index from scratch")
    parser.add_argument("--enable-semantic", action="store_true",
                        help="turn on the second search mode: ensure an embedding model, save "
                             "config, build vectors")
    parser.add_argument("--disable-semantic", action="store_true",
                        help="turn the second search mode off")
    parser.add_argument("--semantic-model", help="model name for --enable-semantic "
                                                 f"(default: {DEFAULT_SEMANTIC_MODEL})")
    parser.add_argument("--no-mirror", action="store_true",
                        help="with --enable-semantic: do not fall back to the model mirror")
    parser.add_argument("--drop-vectors", action="store_true",
                        help="with --disable-semantic: also delete stored vectors")
    parser.add_argument("--install-agent", action="store_true", help="install the updater only")
    parser.add_argument("--uninstall-agent", action="store_true", help="remove the updater")
    parser.add_argument("--no-agent", action="store_true", help="with --setup: skip the updater")
    parser.add_argument("--interval", type=int, default=3600, help="updater interval in seconds")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress hints on stderr")
    return parser


def cmd_search(args) -> int:
    query = " ".join(args.query).strip()
    if not query:
        build_parser().print_help()
        return 2

    maybe_autosync()
    synonyms = load_synonyms()
    results = search(
        query, limit=args.limit, directory=args.dir, days=args.days, synonyms=synonyms
    )

    if args.explain and not args.quiet:
        index_con = connect_index()
        try:
            plan = query_plan(query, idf_map(index_con), synonyms)
        finally:
            index_con.close()
        for concept in plan["concepts"]:
            kind = "topic" if concept["coef"] >= 1.0 else "synonym"
            forms = ", ".join(sorted(concept["members"]))
            print(f"  {kind:8s} {forms}", file=sys.stderr)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
        return 0 if results else 1

    if not results:
        print("No matching sessions. Try other words or a broader topic.")
        return 1

    labels = {"title": "title", "user": "question", "assistant": "answer"}
    for number, item in enumerate(results, 1):
        kind = labels.get(item["matched_in"], "text")
        print(f"{number}. [{item['score']}] {item['date']} — {item['title']}")
        print(f"   {item['directory']}")
        print(
            f"   {item['session_id']}  "
            f"(relevance {item['relevance']:.0%}, chunks {item['hits']}, {kind})"
        )
        print(f"   {item['snippet']}")
        print()

    if args.open:
        binary = shutil.which("opencode")
        if not binary:
            raise RecallError("opencode is not on PATH; open the session by its id instead")
        sys.stdout.flush()
        os.execv(binary, [binary, "-s", results[0]["session_id"]])
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.setup:
        return cmd_setup(install_agent=not args.no_agent, interval=args.interval)
    if args.status:
        return cmd_status()
    if args.enable_semantic:
        return cmd_enable_semantic(args.semantic_model, use_mirror=not args.no_mirror)
    if args.disable_semantic:
        return cmd_disable_semantic(drop_vectors=args.drop_vectors)
    if args.install_agent:
        print(agent.install_agent(interval=args.interval)["message"])
        return 0
    if args.uninstall_agent:
        print(agent.uninstall_agent()["message"])
        return 0
    if args.reindex:
        return cmd_sync(full=True)
    if args.sync:
        return cmd_sync(full=False)
    return cmd_search(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RecallError as exc:
        print(f"{APP}: {exc}", file=sys.stderr)
        sys.exit(3)
    except BrokenPipeError:
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
