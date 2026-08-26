# worktree-gate

Worktree lifecycle operations in this workspace are **gated**: raw `git worktree` /
`git merge` / `git pull` / `git branch -d` from a Bash tool call is blocked, and the
same work goes through an MCP server that records **who did it and why** before
reporting it done.

This exists because worktree ownership used to be *asserted* (`.workboard/worktrees.json`,
hand-maintained, 2 of 46 worktrees claimed). Now it is *observed*.

## Scope — a `.workboard/` defines one

A directory containing `.workboard/` **is a scope**. It governs itself: its own journal, its
own repos, its own worktrees. Three rules follow, and they are what keep two boards from ever
reporting the same worktree:

1. **Never above the workspace.** The gate acts only within the directory it is installed in.
   A `git worktree add` run from anywhere above is not gated and not recorded — it is simply
   none of the gate's business.
2. **A nested scope is ignored entirely.** A subfolder with its own `.workboard/` is skipped
   by the parent's repo discovery, worktree listing and journal. `worktree_status` names what
   it ignored rather than silently omitting it.
3. **The journal belongs to the scope that owns the work.** Events are written to
   `<scope>/.worktree-gate/ledger.jsonl` — resolved from the repo being acted on, or from the
   caller's cwd for a denial. Never to a parent's.

**Ownership follows the repo, not the path.** Worktrees legitimately live in several places
(`<workspace>/.worktrees/`, `<repo>/.worktrees/`, `<repo>/.claude-worktrees/`) and all of them
belong to whoever owns the repo. Each repo's own main checkout is never listed as a worktree.

Read tools take an optional `scope` (a folder relative to the workspace root); omit it for the
workspace itself.

## Parts

| file | what it is |
|---|---|
| `server.py` | stdio MCP server — the sanctioned path. No dependencies. |
| `gate.py` | PreToolUse hook — blocks the raw git verbs, records the attempt |
| `wtledger.py` | append-only ledger + git helpers, shared by both |
| `ledger.jsonl` | **the record** for this scope. One JSON object per event, append-only. Nested scopes keep their own at `<scope>/.worktree-gate/ledger.jsonl` |

## Tools

| tool | does | requires |
|---|---|---|
| `worktree_create` | cuts `.worktrees/<task>-<repo>` on `feature/<task>` from the main checkout's **local HEAD** | `session`, `description`, `repo`, `task` |
| `worktree_pull` | `git pull --ff-only` inside a worktree | `session`, `description`, `repo`, `worktree` |
| `worktree_sync` | fetches, then merges `origin/<branch>` into a repo's **main checkout** | `session`, `description`, `repo`, `branch` |
| `worktree_merge` | merges back into **the branch it was cut from** (read from the ledger), then pushes | `session`, `description`, `repo`, `worktree` |
| `worktree_prune` | removes the worktree and deletes the branch | `session`, `description`, `repo`, `worktree` |
| `worktree_status` | live worktrees with provenance; unattributed listed first | — |
| `worktree_history` | the ledger, filterable | — |

`session` and `description` are mandatory on every mutation and validated **before any git
runs**. Read-only tools require neither — friction on a read is how a tool stops being used.

## What it refuses

- creating when the main checkout is dirty — *"stop and ask"*, never stash someone's work
- merging when the ledger has no base branch for that worktree — states it rather than guessing
- merging when the main checkout is on a different branch than the worktree was cut from
- pruning a worktree with uncommitted changes or unmerged commits (unless `force`, recorded as forced)
- any mutation with a blank `session` or `description`

## If the MCP server isn't in your tool list

Same validation, same ledger entry:

```bash
python3 .worktree-gate/server.py call worktree_create \
  '{"session":"<you>","description":"<why>","repo":"<repo>","task":"<task>"}'
```

## Claims are derived, not exported

A board reads its own scope's journal and derives ownership at build time. There is no
`worktrees.json` to maintain or export, and no way for two boards to disagree.

`DevSession/.workboard/build.py` does this — six lines over `wtledger.read_ledger(SCOPE)` and
`wtledger.live_worktrees(SCOPE)`. The root `.workboard/build.py` still reads the older exported
file and its own `os.listdir` scan; it can adopt the same derivation whenever its owner wants.

## Not gated

`git status`, `log`, `diff`, `worktree list`, `branch` (listing), `add`, `commit`, `push`.
Only create / pull / merge / prune verbs are gated. Scope is this workspace only
(`gate.py` checks `cwd`); other projects keep normal git.


## Why `worktree_sync` exists

The gate governed worktree-to-main merges and nothing else. A main checkout that had diverged
from its remote therefore had no recorded way forward: `git pull` and `git merge` are blocked by
the hook — which is what makes ownership observed rather than asserted — and `worktree_pull`
resolves its target through `live_worktrees`, which only covers worktrees the scope owns, never
the main checkout.

That gap stalled three promotions in one session, each with the work committed locally and no
legitimate way to move it. Two of them were one command from done and stayed blocked for hours.

It fetches and merges. It does **not** force, rebase or reset: a divergence that will not merge
is a conflict for a person to resolve, not something to overwrite. A failed merge is recorded
with `ok: false` and the reason, the same as any other refusal.

Added 2026-08-25. **These files are not under version control** — the directory is untracked and
nothing in `claude-tools` references it — so this change exists on one machine only and has had
no review. That is worth fixing before the gate is relied on elsewhere.
