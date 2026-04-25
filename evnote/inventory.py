from __future__ import annotations

from typing import Iterator

from evernote.edam.notestore.ttypes import NoteFilter, NotesMetadataResultSpec

from . import cache
from .client import Config, call_with_retry, make_client

PAGE_SIZE = 250


def sync(with_content: bool = False, progress=lambda *_: None) -> dict[str, int]:
    """Pull notebooks, tags, and note metadata into the local SQLite cache.

    If ``with_content`` is True, also fetch and cache each note's ENML body
    (Evernote Markup Language). This is much slower and rate-limited.
    """
    cfg = Config.load()
    client = make_client(cfg)
    note_store = client.get_note_store()

    counts = {"notebooks": 0, "tags": 0, "notes": 0, "contents": 0}

    with cache.connect() as conn:
        for nb in call_with_retry(note_store.listNotebooks):
            cache.upsert_notebook(conn, cache.Notebook(nb.guid, nb.name, getattr(nb, "stack", None)))
            counts["notebooks"] += 1
        progress("notebooks", counts["notebooks"])

        for tag in call_with_retry(note_store.listTags):
            cache.upsert_tag(conn, cache.Tag(tag.guid, tag.name, getattr(tag, "parentGuid", None)))
            counts["tags"] += 1
        progress("tags", counts["tags"])

        spec = NotesMetadataResultSpec(
            includeTitle=True,
            includeCreated=True,
            includeUpdated=True,
            includeNotebookGuid=True,
            includeTagGuids=True,
            includeAttributes=True,
        )
        for note_meta in _iter_notes(note_store, spec):
            attrs = note_meta.attributes
            cache.upsert_note(
                conn,
                cache.Note(
                    guid=note_meta.guid,
                    title=note_meta.title or "(untitled)",
                    notebook_guid=note_meta.notebookGuid,
                    created=note_meta.created,
                    updated=note_meta.updated,
                    source_url=getattr(attrs, "sourceURL", None) if attrs else None,
                    tag_guids=list(note_meta.tagGuids or []),
                ),
            )
            counts["notes"] += 1
            if counts["notes"] % 100 == 0:
                progress("notes", counts["notes"])

            if with_content:
                content = call_with_retry(note_store.getNoteContent, note_meta.guid)
                cache.upsert_content(conn, note_meta.guid, content, getattr(note_meta, "contentHash", None))
                counts["contents"] += 1
                if counts["contents"] % 25 == 0:
                    progress("contents", counts["contents"])
        progress("notes", counts["notes"])
        if with_content:
            progress("contents", counts["contents"])

    return counts


def fetch_content(guid: str) -> str:
    """Fetch a single note's ENML body and cache it. Returns the ENML."""
    cfg = Config.load()
    note_store = make_client(cfg).get_note_store()
    content = call_with_retry(note_store.getNoteContent, guid)
    with cache.connect() as conn:
        cache.upsert_content(conn, guid, content, None)
    return content


def _iter_notes(note_store, spec) -> Iterator:
    offset = 0
    while True:
        result = call_with_retry(
            note_store.findNotesMetadata,
            NoteFilter(),
            offset,
            PAGE_SIZE,
            spec,
        )
        notes = result.notes or []
        for n in notes:
            yield n
        offset += len(notes)
        if not notes or offset >= result.totalNotes:
            return
