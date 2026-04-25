# evernote-cli

A command-line tool to **fetch your Evernote notes and reorganize them inside Evernote** — move notes between notebooks, add or remove tags, rename notebooks — all driven by simple rules you write in a YAML file.

> No need to register a developer key. Sign in with your normal Evernote account in your browser, and you're done.

This guide is written for first-time terminal users. Every command goes into the **Terminal** app on your Mac. (To open it: press `⌘ + Space`, type "Terminal", press Enter.)

---

## What you'll need

- A Mac (these instructions are for macOS)
- Your Evernote account login
- About 10 minutes for the first-time setup

---

## Step 1 — Install the prerequisites

You need three things: **Homebrew** (a package manager), **Python**, and **Git**. If you already have all three, skip to Step 2.

### 1a. Install Homebrew

In Terminal, paste this and press Enter:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

It will ask for your Mac password (the same one you use to log in). Type it (the cursor won't move while you type — that's normal) and press Enter. Wait until it finishes.

### 1b. Install Python and Git

```bash
brew install python git
```

### 1c. Verify everything works

```bash
python3 --version
git --version
```

You should see version numbers, not "command not found."

---

## Step 2 — Download evernote-cli

Pick a folder where you want the tool to live. The `Documents` folder is fine:

```bash
cd ~/Documents
git clone https://github.com/sungwookbaek/evernote-cli.git
cd evernote-cli
```

You're now "inside" the project folder.

---

## Step 3 — Install the tool

These two commands set up an isolated Python environment and install everything `evernote-cli` needs:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

Wait for it to finish (it downloads several packages — a minute or two on a normal connection).

After this, the command you'll use is `.venv/bin/evnote`. (If you'd rather just type `evnote`, run `source .venv/bin/activate` first — then for that Terminal session you can drop the `.venv/bin/` prefix.)

---

## Step 4 — Sign in to Evernote

```bash
.venv/bin/evnote login
```

This will:

1. Open Evernote in your default browser.
2. Ask you to sign in (use your normal Evernote email and password).
3. Show an "Authorize" page — click **Authorize**.
4. The browser tab will say "You can close this tab now..." — close it.

Back in Terminal you'll see `Saved token to .env. You're logged in.`

> **What just happened?** The tool got a permission slip from Evernote (called an "auth token") and saved it inside the project folder, in a hidden file called `.env`. That file never leaves your computer, and the project's `.gitignore` makes sure it's never uploaded if you push to GitHub.

---

## Step 5 — Download a copy of all your notes' info

```bash
.venv/bin/evnote inventory
```

This pulls a **catalog** of your notes (titles, notebooks, tags, dates — but NOT the body of the notes) into a small database file on your computer (`.cache/inventory.db`). It might take a few minutes if you have thousands of notes. You'll see counts streaming by.

When it finishes, you'll see something like `Done. {'notebooks': 200, 'tags': 73, 'notes': 7183, 'contents': 0}`.

---

## Step 6 — See what you have

```bash
.venv/bin/evnote list --by notebook
.venv/bin/evnote list --by tag
.venv/bin/evnote list --by year
```

Each command prints a sorted summary of where your notes live. This works **offline** (it reads the local catalog, not Evernote's servers) so you can run it as many times as you want without using your API quota.

---

## Step 7 — Reorganize (the safe way)

The tool reorganizes notes by reading a list of **rules** from a YAML file. We've included an example at `rules/example.yaml`. Open it in TextEdit:

```bash
open -a TextEdit rules/example.yaml
```

A rule looks like this:

```yaml
- name: "Move 2024 receipts into Receipts/2024"
  match:
    tag: receipt
    created_year: 2024
  action:
    move_to_notebook: "Receipts/2024"
```

You can write as many rules as you want. (See [Rule reference](#rule-reference) below.)

### Preview the changes (always do this first)

```bash
.venv/bin/evnote plan rules/example.yaml
```

This prints **what would happen** if you ran the rules — but does not change anything in Evernote. Read it carefully.

### Make a safety backup

```bash
.venv/bin/evnote backup
```

This downloads every note as a `.enex` file (Evernote's official backup format) into `.cache/backups/<timestamp>/`. If anything goes wrong, you can drag those files back into the Evernote app to restore.

### Actually run the rules

```bash
.venv/bin/evnote apply rules/example.yaml --no-dry-run
```

Without `--no-dry-run`, the command only previews. With `--no-dry-run`, it writes the changes to Evernote. The tool refuses to do this unless a fresh backup exists in `.cache/backups/`.

Every change is also recorded in `.cache/audit.log` (one line per change) so you can see exactly what was modified.

---

## Rule reference

Each rule has a **`match`** (which notes does this apply to?) and an **`action`** (what should happen to them?). Rules are checked top-to-bottom; the first one that matches a given note wins.

### Match keys

| Key | Example | Meaning |
|---|---|---|
| `notebook` | `Inbox` | Note is in this notebook |
| `tag` | `receipt` | Note has this tag |
| `tags_all` | `[work, urgent]` | Note has all of these tags |
| `title_regex` | `^Meeting.*` | Note title matches this regex |
| `created_year` | `2024` | Year the note was created |
| `created_month` | `5` | Month the note was created (1-12) |
| `updated_before` | `"2020-01-01"` | Note hasn't been touched since this date |
| `source_url_present` | `true` | Note was clipped from the web |
| `content_contains` | `MAGIC` | Note body contains this text *(requires `evnote inventory --with-content` first — slower)* |

### Action keys

| Key | Example | Meaning |
|---|---|---|
| `move_to_notebook` | `"Receipts/2024"` | Move the note to this notebook (creates it if missing) |
| `add_tags` | `[archived]` | Add these tags to the note |
| `remove_tags` | `[draft]` | Remove these tags from the note |
| `rename_notebook` | `{from: Old, to: New}` | Rename a notebook (rule-level, doesn't need a match) |
| `set_title_template` | `"{year}-{month} {title}"` | Rewrite the title using `{title}`, `{notebook}`, `{year}`, `{month}` |

---

## All commands at a glance

| Command | What it does |
|---|---|
| `evnote login` | Sign in to Evernote in your browser |
| `evnote auth` | Check that your saved sign-in still loads |
| `evnote inventory` | Download note info (no bodies) into a local catalog |
| `evnote inventory --with-content` | Same, plus download every note's full body (slow!) |
| `evnote list --by notebook\|tag\|year` | Show a summary of the catalog |
| `evnote fetch <guid>` | Print one note's body (in Evernote's XML format) |
| `evnote backup` | Save every note as a `.enex` file — required before any `apply` |
| `evnote plan <rules.yaml>` | Preview what your rules would do |
| `evnote apply <rules.yaml>` | Preview only (default — same as `plan`) |
| `evnote apply <rules.yaml> --no-dry-run` | Actually run the rules |

---

## Troubleshooting

**"command not found: evnote"**
You're running it without the `.venv/bin/` prefix. Either always type `.venv/bin/evnote ...`, or run `source .venv/bin/activate` once per Terminal session.

**"command not found: brew"**
Homebrew didn't install correctly, or your Terminal hasn't picked it up. Try opening a new Terminal window. If it still doesn't work, follow the post-install instructions printed at the end of the Homebrew install.

**"EVERNOTE_DEV_TOKEN is not set"**
You haven't logged in yet. Run `.venv/bin/evnote login`.

**The browser opened but says "site can't be reached" after I click Authorize**
That's normal *only if* you ran `login` and then quit Terminal before clicking Authorize. The local helper closes when the command exits. Run `evnote login` again and click Authorize promptly.

**"No backup newer than 24h"**
You're trying to `apply --no-dry-run` without a fresh backup. Run `evnote backup` first.

**I want to undo what `apply` did**
Two options:
1. Read `.cache/audit.log` — every change is recorded there with the old and new state, so you can manually revert in Evernote.
2. Drag the `.enex` files from `.cache/backups/<timestamp>/` into the Evernote app to re-import the original state.

---

## Safety summary

- `evnote apply` previews by default. You must add `--no-dry-run` to actually change anything.
- `--no-dry-run` refuses to run unless `evnote backup` made a fresh ENEX snapshot in the last 24 hours.
- Every executed change is appended to `.cache/audit.log`.
- The auth token, the local catalog, and the backups are all stored under `.env` and `.cache/`, which are gitignored — they never leave your machine.

---

## License

MIT
