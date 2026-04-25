from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Match:
    notebook: str | None = None
    tag: str | None = None
    tags_all: list[str] = field(default_factory=list)
    title_regex: str | None = None
    created_year: int | None = None
    created_month: int | None = None
    updated_before: str | None = None  # ISO date
    source_url_present: bool | None = None
    content_contains: str | None = None  # substring match against cached ENML

    _title_re: re.Pattern | None = field(default=None, init=False, repr=False)
    _updated_before_ms: int | None = field(default=None, init=False, repr=False)

    def __post_init__(self):
        if self.title_regex:
            self._title_re = re.compile(self.title_regex)
        if self.updated_before:
            dt = datetime.fromisoformat(self.updated_before).replace(tzinfo=timezone.utc)
            self._updated_before_ms = int(dt.timestamp() * 1000)


@dataclass
class Action:
    move_to_notebook: str | None = None
    add_tags: list[str] = field(default_factory=list)
    remove_tags: list[str] = field(default_factory=list)
    rename_notebook: dict[str, str] | None = None  # {"from": "...", "to": "..."}
    set_title_template: str | None = None

    def is_empty(self) -> bool:
        return not (
            self.move_to_notebook
            or self.add_tags
            or self.remove_tags
            or self.rename_notebook
            or self.set_title_template
        )


@dataclass
class Rule:
    name: str
    match: Match
    action: Action


def load(path: str | Path) -> tuple[dict, list[Rule]]:
    """Parse a rules YAML.

    Two accepted shapes:
      1. Bare list of rules (legacy):    [- name: ..., match: ..., action: ...]
      2. Mapping with optional defaults: {defaults: {into_stack: ...}, rules: [...]}
    Returns ``(defaults_dict, rule_list)``.
    """
    raw = yaml.safe_load(Path(path).read_text())
    if isinstance(raw, list):
        defaults: dict = {}
        items = raw
    elif isinstance(raw, dict):
        defaults = dict(raw.get("defaults") or {})
        items = raw.get("rules") or []
        if not isinstance(items, list):
            raise ValueError(f"{path}: 'rules' must be a list")
    else:
        raise ValueError(f"{path}: top-level must be a list or a mapping")

    rules: list[Rule] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{path}[{i}]: rule must be a mapping")
        name = item.get("name") or f"rule_{i}"
        match = Match(**(item.get("match") or {}))
        action = Action(**(item.get("action") or {}))
        if action.is_empty():
            raise ValueError(f"{path}[{name}]: action is empty")
        rules.append(Rule(name=name, match=match, action=action))
    return defaults, rules


def matches(rule: Rule, note, notebook_name: str, tag_names: set[str], content: str | None = None) -> bool:
    """Evaluate whether a cached note matches a rule's filter.

    ``content`` is the cached ENML body, looked up only when a rule needs it.
    """
    m = rule.match
    if m.notebook and m.notebook != notebook_name:
        return False
    if m.tag and m.tag not in tag_names:
        return False
    if m.tags_all and not all(t in tag_names for t in m.tags_all):
        return False
    if m._title_re and not m._title_re.search(note.title or ""):
        return False
    if m.created_year is not None or m.created_month is not None:
        if not note.created:
            return False
        dt = datetime.fromtimestamp(note.created / 1000, tz=timezone.utc)
        if m.created_year is not None and dt.year != m.created_year:
            return False
        if m.created_month is not None and dt.month != m.created_month:
            return False
    if m._updated_before_ms is not None and (note.updated or 0) >= m._updated_before_ms:
        return False
    if m.source_url_present is not None:
        has_url = bool(note.source_url)
        if has_url != m.source_url_present:
            return False
    if m.content_contains:
        if not content or m.content_contains not in content:
            return False
    return True


def needs_content(rule_list: list["Rule"]) -> bool:
    return any(r.match.content_contains for r in rule_list)
