# Workboard contract

**This file is the contract between implementations. It must be byte-identical in
`workboard-linux` and `workboard-powershell`.** If it differs between them, the two have
drifted and one of them is wrong.

An implementation is conformant when it produces and consumes exactly the artifacts below. A
board built by one and a ledger written by the other must be interchangeable.

---

## 1. Scope — a `.workboard/` folder defines one

A directory containing `.workboard/` **is a scope**. It governs itself: its own journal, its own
repos, its own worktrees.

1. **Never above the workspace.** An implementation acts only within the directory it is
   installed in. A gated operation targeting anything above is not gated and not recorded.
2. **A nested scope is ignored entirely** — skipped by repo discovery, worktree listing and
   journalling. Status output must **name what it ignored** rather than silently omitting it.
3. **The journal belongs to the scope that owns the work** —
   `<scope>/.worktree-gate/ledger.jsonl`, resolved from the repo being acted on, or from the
   caller's working directory for a denial. Never a parent's.

**Ownership follows the repo, not the path.** Worktrees live in several places
(`<workspace>/.worktrees/`, `<repo>/.worktrees/`, `<repo>/.claude-worktrees/`) and all belong to
whoever owns the repo. A repo's own main checkout is never listed as a worktree.

## 2. The ledger — `<scope>/.worktree-gate/ledger.jsonl`

One JSON object per line, **append only**. Never rewritten, never compacted, never tidied — a
retro-edited audit journal is not an audit journal.

```jsonc
{ "ts": "2026-08-25T09:13:04-05:00",   // ISO 8601 with offset, seconds precision
  "action": "create",                   // create | pull | sync | merge | prune | denied
  "session": "who did it",              // required, non-blank
  "description": "why",                 // required, non-blank
  "repo": "nexus-vcs",                  // null for a denial with no resolved repo
  "worktree": "rc7-lockstep-nexus-vcs",
  "scope": ".",                         // scope path relative to the workspace root
  "ok": true }
```

Action-specific fields, all optional and additive: `branch`, `base`, `path`, `head`, `into`,
`pushed`, `forced`, `adopted`, `originalActor`, `viaTool`, `command`, `cwd`, `suggested`,
`error`, `backfill`.

**A malformed line is an error, not a skipped record.** Read must fail loud and name the line
number. A silently dropped line is a worker who believes it reported and is invisible.

**Keys are sorted on write** so two implementations produce comparable lines.

## 3. Attribution is a precondition, not a post-condition

`session` and `description` are validated **before any git command runs**. An implementation
that validates afterwards can create a worktree and then refuse to record it — producing exactly
the unattributed worktree the tool exists to prevent. This happened once and is why the rule is
written down.

## 4. Refusals — required, and required to be specific

| operation | refuses when | must say |
|---|---|---|
| `create` | the main checkout has uncommitted **tracked** changes | stop and ask — never stash or discard someone's work |
| `create` | the target path already exists | prune it first, or pick another name |
| `merge` | the journal has no `base` for that worktree | state it and demand an explicit target — **never guess** |
| `merge` | the main checkout is on a different branch than the worktree was cut from | name both branches |
| `merge` | either tree is dirty | which one |
| `prune` | uncommitted changes, or commits not in the base branch | show the commits; `force` is permitted and is recorded as `forced` |
| any mutation | blank `session` or `description` | which field, before touching anything |

**Untracked files do not block `create`.** `git worktree add` does not care about them; refusing
on an untracked file alone trains people to route around the gate. Report them as information on
the created worktree instead.

## 5. Board data

`<scope>/.workboard/items.json`

```jsonc
{ "title": "...", "subtitle": "...",
  "groups": [ { "name": "...", "blurb": "...",
                "items": [ { "id": "kebab-slug",          // id == #anchor == notes.item key
                             "title": "...",
                             "status": "open|assigned|blocked|superseded|done",
                             "sev": "live|high|medium|low|design|done",
                             "why": "consequence, not a restatement of the title",
                             "evidence": ["repo :: path/File.cs:78"],
                             "repo": "...", "fix": "...", "was": "prior-id",
                             "expected": ["change  path"], "actual": [],
                             "projectFirst": true, "owner": "...",
                             "links": { "needs": [], "gates": [], "related": [] } } ] } ],
  "sessions": [ { "name": "...", "ref": "...", "role": "...", "holds": "..." } ],
  "questions": [ { "q": "...", "why": "...", "options": ["..."], "rec": "...",
                   "blocks": ["slug"], "who": "..." } ] }
```

`<scope>/.workboard/notes.jsonl` — append only, one object per line:

```jsonc
{ "item": "slug", "who": "...", "session": "...", "worktree": "name or -",
  "ts": "YYYY-MM-DD", "kind": "projection|progress|blocked|done|finding", "note": "..." }
```

## 6. Derived, never stored

- **`awaitingYou`** is recomputed on every build from `questions[].blocks`. It drives both the
  marker and the sort. It is never a field in `items.json`.
- **Worktree ownership** is derived from the ledger at build time. There is no claims file.
- **Per-viewer state** — card order, filters, collapsed sections — lives client-side and never
  reaches `items.json`.

Any status derivable from a relationship must not become a column.

## 7. One writer per layer

| layer | writer |
|---|---|
| `items.json` | the orchestrating session only |
| `notes.jsonl` | anyone, append only |
| `ledger.jsonl` | only the gate, append only |
| the rendered page | generated; nobody edits it |

## 8. Domain experts

A folder holding `.expert.json` and a `CLAUDE.md` declares a **domain expert**: a long-lived
session rooted in that folder, addressed by name, revived when it dies. It is not a worktree
session and not per-task — worktrees hold *work* and are transient; experts hold *knowledge*
and persist.

```jsonc
{ "name": "connections-expert",
  "model": "opus",
  "answers": ["how a connection composes", "which package owns a concern"],
  "covers": ["fractaldataworks/public/src/Fdw.Services.Connections*",
             "reference-servicetypes/public/src/ReferenceConnections*"] }
```

**`covers` is workspace-relative globs, and it is what makes this survive a source-tree
refactor.** The target shape is "the expert owns its folder" — open a session in
`src/connections/` and it knows connections. A flat source tree cannot express that: FDW's
`src/` holds 336 project folders and the connections domain is 29 siblings with no directory
containing them. So the expert names the code it speaks for, independently of where its marker
sits.

When the tree is regrouped by domain, the marker moves into the domain folder and `covers`
collapses to `["**"]` or is dropped — an absent `covers` means the expert's own directory is
its scope. **The marker name, the schema, the tools and the routing are unchanged.** The
refactor is a move, not a migration.

An implementation must:

1. Discover experts by walking for the marker, skipping `.git`, `obj`, `bin`, `node_modules`
   and any scope's own dot-directories.
2. Refuse to revive an expert with no `CLAUDE.md` — a session with no domain rules is worse
   than no expert.
3. Resolve `covers` and report when it matches **nothing**: an expert that speaks for no code
   on disk is a stale declaration, not an expert.
4. Never assert liveness. Discovering running sessions is not something a spawned process can
   do; reviving a live expert produces two. Liveness belongs to the caller.

## 9. Board mutation and subscription

`items.json` is mutated **through the server or not at all**. A PreToolUse hook denies
`Write`/`Edit`/`MultiEdit` on it. `notes.jsonl` is deliberately not guarded: it is append-only
and anyone may add to it.

Creating an item and curating the board are **different acts**, and only the second has one
writer.

| tool | who | what |
|---|---|---|
| `item_file` | anyone | file a NEW item — id, title, why, repo, sev, evidence, fix |
| `item_update` worker fields | anyone | `status` `owner` `actual` `fix` `sev` |
| `item_update` structural fields | the nominated supervisor only | `id` `title` `why` `evidence` `links` `expected` `repo` `was` |

**Why `item_file` is open to everyone.** A gate that refuses a task which is not already an item,
combined with no tool that creates one, is two rules meeting in a place with no door: a session
that *discovers* work can neither record it nor start on it. The "one writer" invariant was never
"only one session may add work" — it exists because several sessions editing one JSON file clobber
each other, and routing through the server solves that. Arrangement, wording and links stay with
the orchestrating session; discovery does not.

`item_file` does **not** take `task`: the id it creates *is* the task. Requiring one would be the
same closed door.

**Who the orchestrating session is comes from configuration**, not from the caller asserting it —
`supervisor.name` in the scope's `.registrar.json`. A flag any caller can pass is a comment, not a
control. With no supervisor nominated, structural fields are refused outright rather than opened
to everyone.

`item_update` accepts only these fields:

| settable by a worker | belongs to the orchestrating session |
|---|---|
| `status` `owner` `actual` `fix` `sev` | `id` `title` `why` `evidence` `links` `expected` `repo` `was` |

An unknown or structural field is **refused by name**. This is what lets many sessions write to
one board without "one writer per layer" degrading into a convention nobody enforces: structure
still has one writer; progress has many.

`<scope>/.workboard/subscriptions.jsonl` — append-only, latest state per (item, subscriber)
wins, so a later unsubscribe cancels an earlier subscribe.
`<scope>/.workboard/notifications.jsonl` — append-only; delivery is recorded as a second
record, never by mutating the first.

### Delivery, and its one honest limit

**A server cannot interrupt a running session.** MCP is request/response; the server has no
channel into a session's context and no way to see which sessions are alive. So delivery is
layered, and only the first layer is guaranteed:

1. **Every tool result carries what is pending for the caller.** Implemented at the single
   dispatch point rather than per tool, so a subscriber cannot miss a notification by using a
   tool nobody thought to instrument. A subscriber learns at its next gated call.
2. `notifications_pending` — the same thing on demand.
3. The `item_update` result names the subscribers and tells the caller to message them. Faster,
   not surer.
4. A dead subscriber is revived like an expert. The queue survives either way.

An implementation must not claim a notification was delivered because it was queued, and must
not drop one because a subscriber appears to be gone.

## 10. Conformance

An implementation is conformant when, against the same `.workboard/` and
`.worktree-gate/ledger.jsonl`:

1. its rendered board is byte-comparable with the other's
2. its ledger lines parse under the other's reader, and vice versa
3. it refuses every case in §4 with a message naming the specific cause
4. it fails loud on a malformed ledger line, naming the line number
5. it names ignored nested scopes in status output
