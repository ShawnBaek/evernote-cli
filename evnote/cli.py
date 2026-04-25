from __future__ import annotations

import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import typer

from . import cache, inventory, planner, rules
from .client import Config
from .executor import BACKUPS_DIR, execute

app = typer.Typer(add_completion=False, help="Fetch and reorganize Evernote notes in-place.")


@app.command()
def login():
    """Sign in to Evernote in your browser and save the auth token to .env."""
    from evernote_backup.cli_app_auth_oauth import get_oauth_client
    from evernote_backup.evernote_client_oauth import (
        EvernoteOAuthCallbackHandler,
        OAuthDeclinedError,
    )

    client = get_oauth_client(backend="evernote", custom_api_data=None)
    handler = EvernoteOAuthCallbackHandler(client, oauth_port=10500, server_host="localhost")
    url = handler.get_oauth_url()
    typer.echo("Opening Evernote in your browser. Click 'Authorize' when prompted.")
    typer.echo(f"If the browser didn't open, paste this URL into it:\n  {url}")

    import webbrowser
    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        token = handler.wait_for_token()
    except OAuthDeclinedError:
        typer.echo("Authorization declined.", err=True)
        raise typer.Exit(2)

    from .client import PROJECT_ROOT
    env_path = PROJECT_ROOT / ".env"
    env_path.write_text(f"EVERNOTE_DEV_TOKEN={token}\nEVERNOTE_SANDBOX=0\n")
    typer.echo(f"Saved token to {env_path}. You're logged in.")


@app.command()
def auth():
    """Verify the saved token loads (does not contact Evernote)."""
    cfg = Config.load()
    typer.echo(f"Token loaded ({len(cfg.token)} chars). Sandbox: {cfg.sandbox}")


@app.command(name="inventory")
def inventory_cmd(
    with_content: bool = typer.Option(
        False, "--with-content/--no-with-content",
        help="Also fetch and cache full ENML content for every note (slow, rate-limited).",
    ),
):
    """Pull all notebooks, tags, and note metadata into the local cache."""
    typer.echo("Syncing inventory..." + (" (with content)" if with_content else ""))
    counts = inventory.sync(
        with_content=with_content,
        progress=lambda kind, n: typer.echo(f"  {kind}: {n}"),
    )
    typer.echo(f"Done. {counts}")


@app.command()
def fetch(
    guid: str = typer.Argument(..., help="Note GUID to fetch."),
    out: Path = typer.Option(None, "--out", help="Write ENML to this path instead of stdout."),
):
    """Fetch a single note's full ENML body and cache it."""
    content = inventory.fetch_content(guid)
    if out:
        out.write_text(content)
        typer.echo(f"Wrote {len(content)} chars → {out}")
    else:
        typer.echo(content)


@app.command(name="list")
def list_cmd(
    by: str = typer.Option("notebook", "--by", help="notebook | tag | year"),
):
    """Summarize the cached inventory."""
    with cache.connect() as conn:
        notebooks = {nb.guid: nb for nb in cache.all_notebooks(conn)}
        tags = {t.guid: t for t in cache.all_tags(conn)}
        notes = cache.all_notes(conn)

    if by == "notebook":
        c: Counter = Counter(
            notebooks[n.notebook_guid].name if n.notebook_guid in notebooks else "?"
            for n in notes
        )
    elif by == "tag":
        c = Counter()
        for n in notes:
            for g in n.tag_guids:
                if g in tags:
                    c[tags[g].name] += 1
    elif by == "year":
        c = Counter(
            (datetime.fromtimestamp(n.created / 1000, tz=timezone.utc).year if n.created else "unknown")
            for n in notes
        )
    else:
        raise typer.BadParameter("--by must be one of: notebook, tag, year")

    for k, v in sorted(c.items(), key=lambda kv: (-kv[1], str(kv[0]))):
        typer.echo(f"  {v:>5}  {k}")
    typer.echo(f"Total notes: {len(notes)}")


@app.command()
def backup():
    """Create an ENEX safety snapshot via evernote-backup."""
    import sys
    # Prefer the evernote-backup binary alongside our Python (works in a venv).
    candidates = [
        Path(sys.executable).parent / "evernote-backup",
        Path(shutil.which("evernote-backup") or ""),
    ]
    en_bk = next((str(p) for p in candidates if p.exists()), None)
    if not en_bk:
        typer.echo(
            "evernote-backup not found. Install with: pip install evernote-backup "
            "(or brew install evernote-backup)",
            err=True,
        )
        raise typer.Exit(2)

    cfg = Config.load()
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = BACKUPS_DIR / ts
    target.mkdir(parents=True, exist_ok=True)
    db = target / "evernote.db"

    typer.echo(f"Snapshot → {target}")
    subprocess.check_call([en_bk, "init-db", "--database", str(db), "--token", cfg.token])
    subprocess.check_call([en_bk, "sync", "--database", str(db)])
    subprocess.check_call([en_bk, "export", "--database", str(db), str(target)])
    typer.echo("Backup complete.")


@app.command()
def plan(rules_path: Path = typer.Argument(..., exists=True, readable=True)):
    """Print what would change if these rules were applied."""
    defaults, rule_list = rules.load(rules_path)
    p = planner.build(rule_list, defaults)
    if not p.note_actions and not p.notebook_actions:
        typer.echo("No matches.")
        return
    if p.default_stack:
        typer.echo(f"  (new notebooks will go into stack: {p.default_stack!r})")
    for nba in p.notebook_actions:
        typer.echo(f"  RENAME notebook {nba.rename_from!r} -> {nba.rename_to!r}  [{nba.rule_name}]")
    for na in p.note_actions:
        typer.echo("  " + _describe(na))
    typer.echo(f"Notes affected: {len(p.note_actions)}; notebook renames: {len(p.notebook_actions)}")


@app.command()
def apply(
    rules_path: Path = typer.Argument(..., exists=True, readable=True),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="Preview only by default."),
):
    """Execute the rules. Defaults to --dry-run; pass --no-dry-run to commit."""
    defaults, rule_list = rules.load(rules_path)
    p = planner.build(rule_list, defaults)
    counts = execute(p, dry_run=dry_run, log=lambda tag, msg: typer.echo(f"[{tag}] {msg}"))
    typer.echo(f"Done. {counts}{' (dry-run)' if dry_run else ''}")


def _describe(na) -> str:
    parts = [f"{na.note_title!r} ({na.note_guid[:8]})"]
    if na.move_to_notebook:
        parts.append(f"move→{na.move_to_notebook!r}")
    if na.add_tags:
        parts.append(f"+{na.add_tags}")
    if na.remove_tags:
        parts.append(f"-{na.remove_tags}")
    if na.new_title:
        parts.append(f"title→{na.new_title!r}")
    parts.append(f"[{na.rule_name}]")
    return " ".join(parts)


if __name__ == "__main__":
    app()
