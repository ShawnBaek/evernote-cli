from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from evernote.edam.type.ttypes import Note as EvNote, Notebook as EvNotebook, Tag as EvTag

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
        notebooks_by_guid = {nb.guid: nb for nb in cache.all_notebooks(conn)}
        tags_by_name = {t.name: t for t in cache.all_tags(conn)}
        notes_by_guid = {n.guid: n for n in cache.all_notes(conn)}

    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    audit = AUDIT_LOG.open("a", buffering=1)  # line-buffered

    try:
        # Notebook renames first so subsequent moves resolve against final names.
        for nba in plan.notebook_actions:
            src = notebooks_by_name.get(nba.rename_from)
            if not src:
                log("SKIP", f"notebook {nba.rename_from!r} not in cache")
                continue
            nb = EvNotebook(guid=src.guid, name=nba.rename_to)
            call_with_retry(note_store.updateNotebook, nb, _log=log)
            _audit(audit, "rename_notebook", {"from": nba.rename_from, "to": nba.rename_to, "guid": src.guid, "rule": nba.rule_name})
            notebooks_by_name[nba.rename_to] = cache.Notebook(src.guid, nba.rename_to, src.stack)
            counts["renamed_nb"] += 1
            log("OK", f"renamed notebook {nba.rename_from!r} -> {nba.rename_to!r}")

        for na in plan.note_actions:
            cached = notes_by_guid.get(na.note_guid)
            if not cached:
                log("SKIP", f"{na.note_guid[:8]} not in cache (re-run inventory)")
                continue

            current_nb_guid = cached.notebook_guid
            current_tags = list(cached.tag_guids)
            current_title = cached.title

            new_nb_guid = current_nb_guid
            new_tags = list(current_tags)
            new_title = current_title

            if na.move_to_notebook:
                target = notebooks_by_name.get(na.move_to_notebook)
                if not target:
                    target = _create_notebook(note_store, na.move_to_notebook, plan.default_stack, _log=log)
                    notebooks_by_name[target.name] = target
                    notebooks_by_guid[target.guid] = target
                new_nb_guid = target.guid

            if na.add_tags or na.remove_tags:
                if na.remove_tags:
                    drop = {tags_by_name[t].guid for t in na.remove_tags if t in tags_by_name}
                    new_tags = [g for g in new_tags if g not in drop]
                if na.add_tags:
                    for name in na.add_tags:
                        tag = tags_by_name.get(name) or _create_tag(note_store, name, _log=log)
                        tags_by_name[tag.name] = tag
                        if tag.guid not in new_tags:
                            new_tags.append(tag.guid)

            if na.new_title:
                new_title = na.new_title

            # Idempotency: if nothing actually changes, skip the API call.
            if (new_nb_guid == current_nb_guid
                and set(new_tags) == set(current_tags)
                and new_title == current_title):
                log("SKIP", f"{na.note_title!r} ({na.note_guid[:8]}) already at target [{na.rule_name}]")
                continue

            # Build a partial Note. Evernote requires title + notebookGuid to be
            # set on every updateNote (BAD_DATA_FORMAT otherwise), so we always
            # populate them from cache. Content and resources stay unset, which
            # tells Evernote to leave them alone.
            n = EvNote(
                guid=na.note_guid,
                title=new_title,
                notebookGuid=new_nb_guid,
                tagGuids=new_tags,
            )
            if new_nb_guid != current_nb_guid:
                counts["moved"] += 1
            if set(new_tags) != set(current_tags):
                counts["tagged"] += 1
            if new_title != current_title:
                counts["retitled"] += 1

            call_with_retry(note_store.updateNote, n, _log=log)
            _audit(audit, "update_note", {
                "guid": na.note_guid,
                "rule": na.rule_name,
                "old": {"notebook_guid": current_nb_guid, "tag_guids": current_tags, "title": current_title},
                "new": {"notebook_guid": new_nb_guid, "tag_guids": new_tags, "title": new_title},
            })
            # Update local view so subsequent rules see the new state.
            notes_by_guid[na.note_guid] = cache.Note(
                guid=na.note_guid, title=new_title, notebook_guid=new_nb_guid,
                created=cached.created, updated=cached.updated,
                source_url=cached.source_url, tag_guids=new_tags,
            )
            log("OK", _describe_note_action(na))
    finally:
        audit.close()

    return counts


def _create_notebook(note_store, name: str, stack: str | None = None, _log=lambda *_: None) -> cache.Notebook:
    nb = EvNotebook(name=name)
    if stack:
        nb.stack = stack
    created = call_with_retry(note_store.createNotebook, nb, _log=_log)
    return cache.Notebook(created.guid, created.name, getattr(created, "stack", None))


def _create_tag(note_store, name: str, _log=lambda *_: None) -> cache.Tag:
    created = call_with_retry(note_store.createTag, EvTag(name=name), _log=_log)
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
