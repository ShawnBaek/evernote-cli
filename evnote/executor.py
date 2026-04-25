from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from evernote.edam.type.ttypes import Notebook as EvNotebook, Tag as EvTag

from . import cache
from .client import CACHE_DIR, Config, call_with_retry, make_client
from .planner import NotebookAction, NoteAction, Plan

BACKUPS_DIR = CACHE_DIR / "backups"
AUDIT_LOG = CACHE_DIR / "audit.log"
BACKUP_MAX_AGE_HOURS = 24


def execute(plan: Plan, dry_run: bool = True, log=lambda *_: None) -> dict[str, int]:
    counts = {"moved": 0, "tagged": 0, "renamed_nb": 0, "retitled": 0}

    if dry_run:
        for na in plan.note_actions:
            log("DRY", _describe_note_action(na))
        for nba in plan.notebook_actions:
            log("DRY", f"rename notebook {nba.rename_from!r} -> {nba.rename_to!r} ({nba.rule_name})")
        return counts

    _require_fresh_backup()

    cfg = Config.load()
    note_store = make_client(cfg).get_note_store()

    with cache.connect() as conn:
        notebooks_by_name = {nb.name: nb for nb in cache.all_notebooks(conn)}
        tags_by_name = {t.name: t for t in cache.all_tags(conn)}

    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    audit = AUDIT_LOG.open("a")

    try:
        # Notebook renames first so subsequent moves resolve against final names.
        for nba in plan.notebook_actions:
            src = notebooks_by_name.get(nba.rename_from)
            if not src:
                log("SKIP", f"notebook {nba.rename_from!r} not in cache")
                continue
            nb = EvNotebook(guid=src.guid, name=nba.rename_to)
            call_with_retry(note_store.updateNotebook, nb)
            _audit(audit, "rename_notebook", {"from": nba.rename_from, "to": nba.rename_to, "guid": src.guid, "rule": nba.rule_name})
            notebooks_by_name[nba.rename_to] = cache.Notebook(src.guid, nba.rename_to, src.stack)
            counts["renamed_nb"] += 1
            log("OK", f"renamed notebook {nba.rename_from!r} -> {nba.rename_to!r}")

        for na in plan.note_actions:
            note = call_with_retry(note_store.getNote, na.note_guid, False, False, False, False)
            old_state = {
                "notebook_guid": note.notebookGuid,
                "tag_guids": list(note.tagGuids or []),
                "title": note.title,
            }

            if na.move_to_notebook:
                target = notebooks_by_name.get(na.move_to_notebook)
                if not target:
                    target = _create_notebook(note_store, na.move_to_notebook, plan.default_stack)
                    notebooks_by_name[target.name] = target
                note.notebookGuid = target.guid
                counts["moved"] += 1

            if na.add_tags or na.remove_tags:
                current = list(note.tagGuids or [])
                if na.remove_tags:
                    drop = {tags_by_name[t].guid for t in na.remove_tags if t in tags_by_name}
                    current = [g for g in current if g not in drop]
                if na.add_tags:
                    for name in na.add_tags:
                        tag = tags_by_name.get(name) or _create_tag(note_store, name)
                        tags_by_name[tag.name] = tag
                        if tag.guid not in current:
                            current.append(tag.guid)
                note.tagGuids = current
                counts["tagged"] += 1

            if na.new_title and na.new_title != note.title:
                note.title = na.new_title
                counts["retitled"] += 1

            call_with_retry(note_store.updateNote, note)
            _audit(audit, "update_note", {
                "guid": na.note_guid,
                "rule": na.rule_name,
                "old": old_state,
                "new": {
                    "notebook_guid": note.notebookGuid,
                    "tag_guids": list(note.tagGuids or []),
                    "title": note.title,
                },
            })
            log("OK", _describe_note_action(na))
    finally:
        audit.close()

    return counts


def _create_notebook(note_store, name: str, stack: str | None = None) -> cache.Notebook:
    nb = EvNotebook(name=name)
    if stack:
        nb.stack = stack
    created = call_with_retry(note_store.createNotebook, nb)
    return cache.Notebook(created.guid, created.name, getattr(created, "stack", None))


def _create_tag(note_store, name: str) -> cache.Tag:
    created = call_with_retry(note_store.createTag, EvTag(name=name))
    return cache.Tag(created.guid, created.name, getattr(created, "parentGuid", None))


def _audit(fp, kind: str, payload: dict) -> None:
    fp.write(json.dumps({"ts": int(time.time()), "kind": kind, **payload}) + "\n")


def _require_fresh_backup() -> None:
    if not BACKUPS_DIR.exists():
        raise RuntimeError(f"No backup found at {BACKUPS_DIR}. Run `evnote backup` first.")
    fresh_cutoff = time.time() - BACKUP_MAX_AGE_HOURS * 3600
    for child in BACKUPS_DIR.iterdir():
        if child.is_dir() and child.stat().st_mtime >= fresh_cutoff:
            return
    raise RuntimeError(
        f"No backup newer than {BACKUP_MAX_AGE_HOURS}h in {BACKUPS_DIR}. "
        "Run `evnote backup` before applying changes."
    )


def _describe_note_action(na: NoteAction) -> str:
    parts = [f"{na.note_title!r} ({na.note_guid[:8]})"]
    if na.move_to_notebook:
        parts.append(f"move→{na.move_to_notebook!r}")
    if na.add_tags:
        parts.append(f"+tags={na.add_tags}")
    if na.remove_tags:
        parts.append(f"-tags={na.remove_tags}")
    if na.new_title:
        parts.append(f"title→{na.new_title!r}")
    parts.append(f"[{na.rule_name}]")
    return " ".join(parts)
