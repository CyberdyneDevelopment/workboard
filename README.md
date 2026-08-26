# workboard

Coordination for a workspace where several AI agent sessions work at once.

It solves two things that rot for the same reason — they need attention exactly when you are
busiest:

- **Worktrees accumulate.** Work finishes, the worktree stays. Nobody remembers who cut it or
  what it was for, so nobody dares prune it.
- **Documentation goes stale silently.** A merge changes a mechanism. The pages describing it
  are still confident and still wrong, and nobody finds out until somebody acts on one.

The fix is the same in both cases: make the moment work moves the moment it gets recorded, and
put that at a chokepoint nobody can go around.

---

## How it works

Raw `git worktree`, `merge`, `pull`, `branch -d` and `commit` are **blocked in the shell**. They
go through an MCP server that does the same git work and records **who did it, why, and which
task it belongs to** — validated *before* anything runs. Read-only git is untouched.

```
you ──▶ supervisor session ──▶ worktree ──▶ commit ──▶ merge ──▶ prune
                  │                            │          │
                  │                            ▼          ▼
                  │                      subscribers   documentation
                  │                      notified      review filed
                  ▼
             domain experts (long-lived, asked questions, revived when dead)
```

Every worktree ends the same way — pruned. The only question is whether it merges first, and
both paths are recorded, so an abandoned experiment is a decision on the record rather than a
directory nobody will touch.

## What it gives you

### Attribution that cannot be skipped

`session`, `description` and `task` are required on every mutation and checked **before** git
runs. The task must name a real board item or a tracker issue key — a free-text field decays
into "misc" within a week, and then the ledger tells you a worktree existed but not what it was
for.

Everything lands in an append-only `ledger.jsonl`. Nothing is ever rewritten.

### A board that many sessions can write to

`items.json` holds the work; `notes.jsonl` is append-only and open to anyone. Direct edits to
`items.json` are **blocked** — it changes through `item_update`, which accepts `status`, `owner`,
`actual`, `fix` and `sev`, and refuses structural fields by name. Structure keeps one writer;
progress gets many.

### Subscriptions and notifications

Subscribe a session to a board item and it is told when the item moves — on update, and on every
commit against its task.

Delivery is layered, and the docs are honest about which layer is guaranteed:

| | |
|---|---|
| every tool result carries what is pending for its caller | **guaranteed** |
| a cheap headless session relays it to whoever is live | fast, best-effort |
| `notifications_pending` on demand | guaranteed |

A server cannot interrupt a running session — so it starts one that can. A subscriber it cannot
reach keeps its notification queued and collects at its next call. Nothing is dropped because a
session looked absent.

### A documentation loop that files itself

A merge captures the diff and the commit subjects **before** merging (afterwards the range is
empty), then routes the change to whichever documentation project declares coverage of those
paths, and starts a session there to record it.

Routing is declared, not hardcoded. Each documentation project owns a `coverage.json`:

```jsonc
{ "project": "fdw-anatomy", "agent": "fdw-anatomy-maintainer", "precedence": 10,
  "covers":  [{ "repo": "fractaldataworks", "paths": ["public/src/**"] }],
  "ignores": ["**/*.Tests/**", "**/obj/**"] }
```

A change **nothing** covers is queued in `_unrouted.jsonl` as a finding — never sent to a
default project. A subsystem no document covers is exactly the case you most need to see.

The spawned session follows that project's own `CLAUDE.md`, which **outranks** the prompt it was
given. In a registrar-style corpus it records the claim and marks pages suspect; it does not
rewrite a page to match a diff.

### Domain experts

A folder holding `.expert.json` and a `CLAUDE.md` is a long-lived expert session — asked
questions, addressed by name, revived when it dies. `covers` names the code it speaks for, so it
works with a flat source tree today and collapses to the folder itself when the tree is grouped
by domain.

### Scopes

A directory containing `.workboard/` is a scope: its own board, journal and worktrees. A nested
scope governs itself and the parent ignores it entirely — that is how you separate a fork or a
product line without two boards disagreeing about who owns what.

## Quick start

```bash
linux/registrar.sh doctor .           # audit a workspace; exits non-zero if wrong
linux/registrar.sh init-scope ~/work  # create one
linux/registrar.sh add-project myrepo --docs my-corpus
linux/registrar.sh sweep . 30         # loose files, classified with the evidence shown
```

Then register the hook and the MCP server — `init-scope` prints both snippets. It never edits
your configuration for you.

## Two implementations

| | `linux/` (Python 3) | `powershell/` (PowerShell 7+) |
|---|---|---|
| gate hook | ~21 ms per call | ~560 ms per call |
| MCP server | 20 tools | core worktree tools; rest in progress |
| board builder | yes | yes, byte-comparable output |

The hook fires on **every** shell command, so that 27× gap is real — 300 commands is about three
minutes of pure latency. **On a machine that has Python, use the Python hook even if the rest of
your world is PowerShell.** They are separate processes sharing only the ledger file.

**[`SPEC.md`](SPEC.md) is the contract** — ledger format, scope rules, refusal semantics, board
shapes, expert declaration, delivery guarantees. If the two implementations ever disagree, one of
them is wrong and `SPEC.md` says which.

## Layout

```
SPEC.md              the contract
linux/               registrar.sh · install.sh · worktree-gate/ · board/
powershell/          Registrar.ps1 · gate/ · board/
shared/board/        template.html · items.example.json
vscode-extension/    board viewer, plain JS, no build step
```

## Status

Working: the gate, attribution, task validation, the board with subscriptions and notifications,
coverage routing, the unrouted queue, documentation review with spawned registrars, domain
experts.

In progress: the remaining PowerShell MCP tools. The transport is proven and the gate hook is at
full parity with Python.
