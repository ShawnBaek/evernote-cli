from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .client import CACHE_DIR

DB_PATH = CACHE_DIR / "inventory.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS notebooks (
    guid TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    stack TEXT
);
CREATE TABLE IF NOT EXISTS tags (
    guid TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    parent_guid TEXT
);
CREATE TABLE IF NOT EXISTS notes (
    guid TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    notebook_guid TEXT NOT NULL,
    created INTEGER,
    updated INTEGER,
    source_url TEXT,
    FOREIGN KEY(notebook_guid) REFERENCES notebooks(guid)
);
CREATE TABLE IF NOT EXISTS note_tags (
    note_guid TEXT NOT NULL,
    tag_guid TEXT NOT NULL,
    PRIMARY KEY(note_guid, tag_guid)
);
CREATE TABLE IF NOT EXISTS note_contents (
    note_guid TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    content_hash TEXT,
    fetched_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_notebook ON notes(notebook_guid);
CREATE INDEX IF NOT EXISTS idx_notes_created ON notes(created);
"""


@dataclass
class Notebook:
    guid: str
    name: str
    stack: str | None


@dataclass
class Tag:
    guid: str
    name: str
    parent_guid: str | None


@dataclass
class Note:
    guid: str
    title: str
    notebook_guid: str
    created: int | None
    updated: int | None
    source_url: str | None
    tag_guids: list[str]


@contextmanager
def connect():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_notebook(conn: sqlite3.Connection, nb: Notebook) -> None:
    conn.execute(
        "INSERT INTO notebooks(guid, name, stack) VALUES (?,?,?) "
        "ON CONFLICT(guid) DO UPDATE SET name=excluded.name, stack=excluded.stack",
        (nb.guid, nb.name, nb.stack),
    )


def upsert_tag(conn: sqlite3.Connection, tag: Tag) -> None:
    conn.execute(
        "INSERT INTO tags(guid, name, parent_guid) VALUES (?,?,?) "
        "ON CONFLICT(guid) DO UPDATE SET name=excluded.name, parent_guid=excluded.parent_guid",
        (tag.guid, tag.name, tag.parent_guid),
    )


def upsert_note(conn: sqlite3.Connection, n: Note) -> None:
    conn.execute(
        "INSERT INTO notes(guid, title, notebook_guid, created, updated, source_url) "
        "VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(guid) DO UPDATE SET title=excluded.title, "
        "notebook_guid=excluded.notebook_guid, created=excluded.created, "
        "updated=excluded.updated, source_url=excluded.source_url",
        (n.guid, n.title, n.notebook_guid, n.created, n.updated, n.source_url),
    )
    conn.execute("DELETE FROM note_tags WHERE note_guid=?", (n.guid,))
    if n.tag_guids:
        conn.executemany(
            "INSERT INTO note_tags(note_guid, tag_guid) VALUES (?,?)",
            [(n.guid, t) for t in n.tag_guids],
        )


def all_notebooks(conn: sqlite3.Connection) -> list[Notebook]:
    return [Notebook(r["guid"], r["name"], r["stack"]) for r in conn.execute("SELECT * FROM notebooks")]


def all_tags(conn: sqlite3.Connection) -> list[Tag]:
    return [Tag(r["guid"], r["name"], r["parent_guid"]) for r in conn.execute("SELECT * FROM tags")]


def all_notes(conn: sqlite3.Connection) -> list[Note]:
    rows = list(conn.execute("SELECT * FROM notes"))
    tag_map: dict[str, list[str]] = {}
    for r in conn.execute("SELECT note_guid, tag_guid FROM note_tags"):
        tag_map.setdefault(r["note_guid"], []).append(r["tag_guid"])
    return [
        Note(
            guid=r["guid"],
            title=r["title"],
            notebook_guid=r["notebook_guid"],
            created=r["created"],
            updated=r["updated"],
            source_url=r["source_url"],
            tag_guids=tag_map.get(r["guid"], []),
        )
        for r in rows
    ]


def upsert_content(conn: sqlite3.Connection, guid: str, content: str, content_hash: str | None) -> None:
    import time as _time
    conn.execute(
        "INSERT INTO note_contents(note_guid, content, content_hash, fetched_at) VALUES (?,?,?,?) "
        "ON CONFLICT(note_guid) DO UPDATE SET content=excluded.content, "
        "content_hash=excluded.content_hash, fetched_at=excluded.fetched_at",
        (guid, content, content_hash, int(_time.time())),
    )


def get_content(conn: sqlite3.Connection, guid: str) -> str | None:
    row = conn.execute("SELECT content FROM note_contents WHERE note_guid=?", (guid,)).fetchone()
    return row["content"] if row else None


def notebook_by_name(conn: sqlite3.Connection, name: str) -> Notebook | None:
    row = conn.execute("SELECT * FROM notebooks WHERE name=?", (name,)).fetchone()
    return Notebook(row["guid"], row["name"], row["stack"]) if row else None


def tag_by_name(conn: sqlite3.Connection, name: str) -> Tag | None:
    row = conn.execute("SELECT * FROM tags WHERE name=?", (name,)).fetchone()
    return Tag(row["guid"], row["name"], row["parent_guid"]) if row else None
