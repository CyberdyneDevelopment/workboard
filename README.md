# workboard

Coordination tooling for running several agents across several repos.

| part | what it does |
|---|---|
| **`worktree-gate/`** | Blocks raw `git worktree` / `merge` / `pull` / `branch -d` from agents and routes them through an MCP server that records **who did it and why** before reporting it done. Worktree ownership becomes *observed* instead of *declared*. |
| **`board/`** | Turns `items.json` + append-only `notes.jsonl` into a single `WORKBOARD.html` — items, evidence, expected-vs-actual file lists, per-item note logs, a dependency map, and a question queue for the human. |
| **`vscode-extension/`** | The board inside VS Code: items in an editor panel, Questions / Map / Sessions / Worktrees as collapsible side views you can drag to the right sidebar, clickable file locations, auto-refresh. |

Python 3 and plain JavaScript. **No npm, no TypeScript, no build step, no dependencies.**

This is a staging ground — these capabilities are headed for the DevSession product
(`nexus-vcs` working copies + `nexus-track` items). Until then they live here so they can be
installed on any machine.

## Install

```bash
./install.sh /path/to/workspace --extension
```

Links `.worktree-gate/` and `.workboard/build.py` into the workspace, seeds an `items.json` if
there isn't one, and (with `--extension`) links the extension into `~/.vscode-server/extensions`
or `~/.vscode/extensions`.

It deliberately **does not** edit your MCP config or your Claude Code hooks — it prints those
two snippets for you to paste. A tool that silently rewrites your settings is worse than one
that asks.

Then: `python3 <workspace>/.workboard/build.py`

## How it hangs together

```
   agent runs `git worktree add ...`
              │
              ▼
   gate.py  (PreToolUse hook)  ──► DENIED, and the attempt is recorded
              │
              ▼
   worktree_create(session, description, repo, task)      ← the only sanctioned path
              │
              ├─► git, via the same commands a human would run
              └─► append to <scope>/.worktree-gate/ledger.jsonl
                              │
                              ▼
                     build.py derives ownership ──► WORKBOARD.html
                              │
                              ▼
                     VS Code extension reads the data directly
```

Nothing is declared twice. The journal is the record; the board and the extension are views of
it. There is no `worktrees.json` to maintain and no way for two views to disagree.

## Scope — a `.workboard/` folder defines one

A directory containing `.workboard/` governs itself: its own journal, its own repos, its own
worktrees. The gate never acts above its workspace root, ignores any nested folder that has its
own `.workboard/` (and *names* what it ignored rather than silently omitting it), and writes
each event to the journal of the scope that owns the repo.

Ownership follows the **repo**, not the path — a worktree under `<repo>/.worktrees/` counts the
same as one under `<workspace>/.worktrees/`, and a repo's own main checkout is never a worktree.

## What the gate refuses

- creating a worktree when the main checkout has uncommitted **tracked** changes
- merging when the journal has no base branch for that worktree — it says so rather than
  guessing a target
- merging when the main checkout is on a different branch than the worktree was cut from
- pruning with uncommitted or unmerged work, unless forced (and forced is recorded)
- any mutation with a blank `session` or `description`, checked **before** any git runs

## Read this before changing it

`CLAUDE.md` carries the working rules — one writer per layer, derive rather than store, absence
is displayed not hidden, and never weaken a refusal to unblock your own work.

## Status

Working and in daily use, but young. Known rough edges:

- The gate refuses on **untracked-only** changes, which `git worktree add` does not care about.
  Fix is drafted (`--untracked-files=no` for the refusal, untracked reported as a note) and
  deliberately unapplied — relaxing a guardrail is the operator's call.
- `board/build.py` renders a fixed layout; the extension is the more flexible view.
- The extension's activation path has been exercised by hand, not by an automated test.
