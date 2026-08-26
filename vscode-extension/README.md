# Workboard — VS Code extension

A live view of any `.workboard/` board, inside the editor. Reads the board **data** directly
(`items.json`, `notes.jsonl`, the worktree-gate journal) rather than scraping the generated
`WORKBOARD.html`, so it restructures the page for an editor and does not break when `build.py`
changes.

Plain JavaScript. **No TypeScript, no npm install, no build step** — edit a file, reload the
window, done.

## Layout

| surface | what |
|---|---|
| **Editor panel** (`Workboard: Open board`) | a summary strip — open · live · blocked · awaiting you · questions · notes · shipped — then the items: grouped, severity/status chips, evidence, expected vs actual, collapsible note logs |
| **Side container** (activity bar; drag to the **right** sidebar) | **Map**, **Sessions**, **Worktrees** as collapsible sections |
| **Bottom panel** — *Workboard Questions* | the question queue, with the recommendation marked and clickable links to the items each one unblocks |

The three side views are separate webviews in one container, so VS Code renders them as
collapsible sections; drag the container into the secondary (right) sidebar to keep them there.
Questions live in the **bottom panel** instead, where they sit alongside the terminal and
problems — a decision queue you glance at, not a column competing with the work.

VS Code has no manifest key for defaulting a container to the right sidebar, so the side
container starts in the activity bar and stays wherever you drag it. The bottom panel
placement *is* declared, so Questions lands there with no setup.

## File locations are clickable

Anything in the board that looks like a file opens in the editor at the right line:

```
reference-servicetypes :: ReferenceSecretManagers.MsSql/MsSqlSecretManagerType.cs:78
.worktree-gate/server.py
NEXUS-BOUNDARY.md
```

Resolution order, first hit wins:

1. `<repo>/<mid>/<path>` for every repo in the workspace, where `<mid>` is one of
   `""`, `public/src`, `public`, `src`, `public/tests`, `tests`. A `repo ::` prefix is tried
   first, via `workboard.repoAliases` (`FDW` → `fractaldataworks` by default), then an
   exact case-insensitive match, then a substring match, then every other repo.
2. board-relative, then workspace-relative.
3. a workspace search on the basename, scored by longest matching path suffix.

If nothing matches it says so and names what it tried — it never opens the wrong file silently.

Measured against the two real boards in this workspace: **64 of 106 locations resolve directly**,
and every remaining one is a bare basename that is unique in the workspace, so the search
fallback finds it.

Links in the **Map** and **Questions** views jump to the item in the main panel, opening it if
it is not already open.

## Auto-refresh

Watches `**/.workboard/items.json`, `**/.workboard/notes.jsonl` and
`**/.worktree-gate/ledger.jsonl`. Any change re-renders every view (debounced 150 ms). Open
`<details>` sections stay open across a refresh — a re-render must not collapse what you opened.

Set `workboard.autoRebuild` to also run the board's `build.py` on each data change, so the
standalone `WORKBOARD.html` keeps up too. The extension's own views do not need it.

**`Workboard: Open full page`** shows the generated `WORKBOARD.html` whole, in a webview. While
that view is open the board is rebuilt on every data change regardless of `autoRebuild` — it
shows the *generated* page, so leaving it stale would make the command look broken. Scroll
position survives each reload; a monitor that jumps to the top on every refresh is useless.

## Several boards

Every folder with a `.workboard/items.json` is a board. `Workboard: Switch board` picks one;
`workboard.boardPath` pins one. The **Worktrees** view shells out to the worktree gate
(`server.py call worktree_status --scope <board>`) rather than re-implementing scope rules, so
each board shows only the worktrees its own scope owns.

## Install

```bash
ln -s /home/mike/projects/cyberdynedevelopment/DevSession/workboard-vscode \
      ~/.vscode-server/extensions/cyberdine.workboard-0.1.0
```

Then **Developer: Reload Window**. (Use `~/.vscode/extensions/` for a local, non-Remote-SSH
VS Code.) To develop it: `code --extensionDevelopmentPath=/home/mike/projects/cyberdynedevelopment/DevSession/workboard-vscode`.

Living under `DevSession/` changes nothing at runtime — the extension resolves repos and boards
from the VS Code **workspace folder**, not from where its own code sits. It has no `.workboard/`
of its own, so it does not become a scope.

## Commands

| command | does |
|---|---|
| `Workboard: Open board` | open the main panel |
| `Workboard: Refresh` | re-read and re-render now |
| `Workboard: Switch board` | pick which `.workboard/` to show |
| `Workboard: Rebuild HTML page` | run this board's `build.py` |
| `Workboard: Open full page (auto-refreshing)` | the whole generated `WORKBOARD.html` in a webview, reloaded on every change |

## Settings

| setting | default | what |
|---|---|---|
| `workboard.boardPath` | `""` | pin a board; empty = auto |
| `workboard.autoRebuild` | `false` | run `build.py` on data change |
| `workboard.pythonPath` | `python3` | interpreter for `build.py` and the gate |
| `workboard.repoAliases` | `{"FDW": "fractaldataworks"}` | map a `repo ::` prefix to a directory |

## Files

| file | what |
|---|---|
| `extension.js` | activation, board discovery, watchers, commands, file resolution |
| `render.js` | data → HTML for each view, plus linkification |
| `package.json` | manifest; **no dependencies** |
