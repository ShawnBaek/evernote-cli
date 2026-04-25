from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from . import cache, rules


@dataclass
class NoteAction:
    """A pending change for a single note."""
    note_guid: str
    note_title: str
    rule_name: str
    move_to_notebook: str | None = None
    add_tags: list[str] | None = None
    remove_tags: list[str] | None = None
    new_title: str | None = None


@dataclass
class NotebookAction:
    rule_name: str
    rename_from: str
    rename_to: str


@dataclass
class Plan:
    note_actions: list[NoteAction]
    notebook_actions: list[NotebookAction]
    default_stack: str | None = None


def build(rule_list: list[rules.Rule], defaults: dict | None = None) -> Plan:
    note_actions: list[NoteAction] = []
    notebook_actions: list[NotebookAction] = []
    needs_content = rules.needs_content(rule_list)

    with cache.connect() as conn:
        notebooks = {nb.guid: nb for nb in cache.all_notebooks(conn)}
        tags = {t.guid: t for t in cache.all_tags(conn)}
        notes = cache.all_notes(conn)
        contents: dict[str, str] = {}
        if needs_content:
            for r in conn.execute("SELECT note_guid, content FROM note_contents"):
                contents[r["note_guid"]] = r["content"]

    # Notebook-level actions (rename) run independently of note matching.
    for r in rule_list:
        if r.action.rename_notebook:
            notebook_actions.append(
                NotebookAction(
                    rule_name=r.name,
                    rename_from=r.action.rename_notebook["from"],
                    rename_to=r.action.rename_notebook["to"],
                )
            )

    note_rules = [r for r in rule_list if not r.action.rename_notebook]

    for note in notes:
        nb = notebooks.get(note.notebook_guid)
        nb_name = nb.name if nb else ""
        tag_names = {tags[g].name for g in note.tag_guids if g in tags}

        content = contents.get(note.guid) if needs_content else None
        for r in note_rules:
            if not rules.matches(r, note, nb_name, tag_names, content):
                continue
            new_title = _apply_title_template(note, nb_name, r.action.set_title_template) if r.action.set_title_template else None
            note_actions.append(
                NoteAction(
                    note_guid=note.guid,
                    note_title=note.title,
                    rule_name=r.name,
                    move_to_notebook=r.action.move_to_notebook,
                    add_tags=list(r.action.add_tags) if r.action.add_tags else None,
                    remove_tags=list(r.action.remove_tags) if r.action.remove_tags else None,
                    new_title=new_title,
                )
            )
            break  # first match wins

    default_stack = (defaults or {}).get("into_stack")
    return Plan(
        note_actions=note_actions,
        notebook_actions=notebook_actions,
        default_stack=default_stack,
    )


def _apply_title_template(note: cache.Note, notebook_name: str, template: str) -> str:
    dt = datetime.fromtimestamp((note.created or 0) / 1000, tz=timezone.utc) if note.created else None
    return template.format(
        title=note.title,
        notebook=notebook_name,
        year=dt.year if dt else "",
        month=f"{dt.month:02d}" if dt else "",
    )
