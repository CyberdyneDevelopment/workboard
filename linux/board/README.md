# board

Turns a board's data into a page. Two builders — one Python, one PowerShell — that read the
same inputs, fill the same template, and produce **byte-identical output**, so you can diff one
against the other as a regression test.

## The files

| file | what |
|---|---|
| `template.html` | the whole design: palette, type, layout, and the per-viewer script. Both builders fill its `{{TOKENS}}`. Edit this to change how a board looks. |
| `build-artifact.py` | Python builder. `python3 build-artifact.py <board-dir>` |
| `Build-WorkboardArtifact.ps1` | PowerShell builder. Windows PowerShell 5.1 and PowerShell 7+, no modules. |
| `build.py` | the older full builder — shells out to git for a live worktree table and emits a standalone `<!doctype html>` page. Keep it for local use; it is **not** artifact-safe. |
| `items.example.json` | the schema, with every field shown once. |

## What a board is

A folder containing `.workboard/`:

```
<board>/
  .workboard/
    items.json                  the catalogue — one writer, the orchestrator
    notes.jsonl                 append-only, anyone, one JSON object per line
    worktrees-snapshot.json     optional; see below
```

`items.json` takes an optional `title` and `subtitle` at the top level — they become the page's
name and standfirst. Without them the page is called "Workboard".

## Build

```bash
python3 build-artifact.py /path/to/board            # -> <board>/workboard-artifact.html
python3 build-artifact.py /path/to/board -o out.html
```

```powershell
.\Build-WorkboardArtifact.ps1 -Board C:\path\to\board
.\Build-WorkboardArtifact.ps1 -Board C:\path\to\board -Out out.html
```

Pass `--stamp` / `-Stamp` to pin the footer's build time — needed if you want the two builders
to produce identical bytes, since otherwise each stamps its own clock.

## Two things the artifact builder does differently

**No `<!doctype>`/`<html>`/`<head>` wrapper.** The Artifact host supplies that. A page that
brings its own renders nested and broken, which is the single most common way a working local
page fails once published.

**No git.** An artifact cannot run git wherever it lands, so the worktree table comes from an
optional `.workboard/worktrees-snapshot.json` and is labelled a snapshot rather than pretended
to be live:

```json
[ { "repo": "nexus-vcs", "name": "feature-x-nexus-vcs",
    "branch": "feature/x", "owner": "session-name" } ]
```

Omit the file and the panel simply does not appear. **A stale panel claiming to be live is worse
than no panel** — that is why it is opt-in and labelled.

## Publishing it

The output is a single file with everything inlined. Its only external reference is Google
Fonts, which is the one host the Artifact CSP admits.

Publish it, then to update: edit `items.json` or append to `notes.jsonl`, rebuild, and republish
**to the same URL**. Same link, new content.

## Verifying the two builders agree

```bash
python3 build-artifact.py <board> -o /tmp/py.html --stamp T
pwsh -File Build-WorkboardArtifact.ps1 -Board <board> -Out /tmp/ps.html -Stamp T
diff /tmp/py.html /tmp/ps.html && echo identical
```

They are kept identical deliberately: the same escaping (`&#x27;`, not `&#39;`), the same
literal `·` and `—` rather than entities, and explicit tiebreaks on cluster and hub ordering so
neither depends on its language's sort semantics. If that diff ever fails, one of them drifted.

## What it renders

Tallies · the question queue with `awaiting you` derived from `questions[].blocks` · every group
and item with evidence, expected-vs-actual file lists and its note log · the dependency map from
`links` · collapsible session and worktree panels.

`awaiting you` is **derived on every build** and is never stored. Answer a question, delete it,
and the flag and the sort disappear on their own. Open/closed `<details>` state is per-viewer in
`localStorage` and never reaches the record.
