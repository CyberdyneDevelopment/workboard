#!/usr/bin/env python3
"""Regenerate WORKBOARD.html from items.json + notes.jsonl.

Workers never edit the HTML. They append one JSON object per line to notes.jsonl
and run this. See README.md for the note format.
"""
import html
import json
import os
import subprocess
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "WORKBOARD.html")
# Why: this board lives in a subfolder, but the repos it tracks live in the workspace
# root. Scan there, render here.
WORKSPACE = os.path.dirname(ROOT)

SEV_LABEL = {
    "live": "live bug",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "design": "decision",
    "done": "shipped",
}
STATUS_LABEL = {
    "open": "open",
    "assigned": "assigned",
    "blocked": "blocked",
    "superseded": "superseded",
    "done": "done",
}


def esc(text):
    return html.escape(str(text), quote=True)


def load_notes():
    notes = defaultdict(list)
    path = os.path.join(HERE, "notes.jsonl")
    if not os.path.exists(path):
        return notes
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            notes[entry.get("item", "?")].append(entry)
    return notes


sys.path.insert(0, os.path.join(WORKSPACE, ".worktree-gate"))
try:
    import wtledger as L  # noqa: E402
except ImportError:
    # Why optional: a board is useful on its own. Without the worktree gate installed
    # there is simply no worktree table -- not a broken page.
    L = None

SCOPE = ROOT   # this board governs its own folder


def worktree_claims():
    if L is None:
        return {}
    """Who owns a worktree, derived from THIS scope's journal.

    One scope, one journal, one board. A parent's journal is not consulted -- borrowing
    it is how two boards come to report the same worktree and disagree about its owner."""
    claims, pruned = {}, set()
    for row in L.read_ledger(SCOPE):
        if not row.get("ok", True) or not row.get("repo"):
            continue
        key = f"{row['repo']}/{row['worktree']}"
        if row.get("action") == "create":
            claims[key] = row["session"]; pruned.discard(key)
        elif row.get("action") == "prune":
            pruned.add(key)
    return {k: v for k, v in claims.items() if k not in pruned}


def git_worktrees():
    """Worktrees this scope owns. Nested scopes govern themselves and are excluded."""
    if L is None:
        return []
    claims = worktree_claims()
    return [(repo, name, branch, claims.get(f"{repo}/{name}", ""))
            for repo, name, branch, _ in L.live_worktrees(SCOPE)]


def render_item(item, notes):
    sev = item.get("sev", "medium")
    status = item.get("status", "open")
    parts = [
        f'<article class="item sev-{esc(sev)} st-{esc(status)}" id="{esc(item["id"])}" draggable="true" data-id="{esc(item["id"])}">',
        f'  <a class="slug" href="#{esc(item["id"])}">{esc(item["id"])}</a>',
        '  <header class="item-head">',
        f'    <h3>{esc(item["title"])}</h3>',
        '    <span class="chips">',
        f'      <span class="chip chip-sev">{esc(SEV_LABEL.get(sev, sev))}</span>',
        f'      <span class="chip chip-st">{esc(STATUS_LABEL.get(status, status))}</span>',
        ('      <span class="chip chip-you">awaiting you</span>' if item.get("awaitingYou") else ''),
        '    </span>',
        '  </header>',
        f'  <p class="why">{esc(item["why"])}</p>',
    ]

    if item.get("evidence"):
        parts.append('  <ul class="evidence">')
        for line in item["evidence"]:
            parts.append(f'    <li><code>{esc(line)}</code></li>')
        parts.append('  </ul>')

    meta = []
    if item.get("fix"):
        meta.append(("fix", item["fix"]))
    if item.get("repo"):
        meta.append(("repo", item["repo"]))
    if item.get("owner"):
        meta.append(("owner", item["owner"]))
    if meta:
        parts.append('  <dl class="meta">')
        for key, value in meta:
            parts.append(f'    <dt>{esc(key)}</dt><dd>{esc(value)}</dd>')
        parts.append('  </dl>')

    expected = item.get("expected") or []
    actual = item.get("actual") or []
    if expected or actual or item.get("projectFirst"):
        parts.append('  <div class="files">')
        if item.get("projectFirst"):
            parts.append('    <p class="project-first">Project the impact BEFORE opening files &mdash; '
                         'list every file you expect to create, delete or change, post it as a note, then work.</p>')
        if expected:
            parts.append(f'    <details><summary>expected files <b>{len(expected)}</b></summary><ul>')
            for line in expected:
                parts.append(f'      <li><code>{esc(line)}</code></li>')
            parts.append('    </ul></details>')
        if actual:
            parts.append(f'    <details><summary>actual changes <b>{len(actual)}</b></summary><ul>')
            for line in actual:
                parts.append(f'      <li><code>{esc(line)}</code></li>')
            parts.append('    </ul></details>')
        elif expected:
            parts.append('    <details><summary>actual changes <b>0</b></summary>'
                         '<p class="empty">Nothing recorded yet. Filled from <code>git diff --stat</code> when the work lands.</p></details>')
        parts.append('  </div>')

    entries = notes.get(item["id"], [])
    parts.append('  <details class="log">')
    parts.append(f'    <summary>notes <b>{len(entries)}</b></summary>')
    if entries:
        parts.append('    <ol>')
        for entry in entries:
            who = entry.get("who", "?")
            when = entry.get("ts", "")
            where = entry.get("worktree") or entry.get("session") or ""
            parts.append(
                '      <li><span class="byline">'
                f'<b>{esc(who)}</b>'
                + (f' <span class="where">{esc(where)}</span>' if where else "")
                + (f' <time>{esc(when)}</time>' if when else "")
                + f'</span>{esc(entry.get("note", ""))}</li>')
        parts.append('    </ol>')
    else:
        parts.append('    <p class="empty">No notes yet.</p>')
    parts.append('  </details>')
    parts.append('</article>')
    return "\n".join(parts)



def render_questions(questions):
    if not questions:
        return ""
    out = ['<section class="questions" id="questions">',
           f'  <h2>For you to answer <span class="qcount">{len(questions)}</span></h2>',
           '  <p class="blurb">Nothing here is blocked on work. Each one is a decision only you can make, '
           'with what I would do and why.</p>']
    for n, q in enumerate(questions, 1):
        out.append('  <article class="q">')
        out.append(f'    <h3><span class="qnum">{n}</span>{esc(q["q"])}</h3>')
        out.append(f'    <p class="why">{esc(q["why"])}</p>')
        out.append('    <ul class="opts">')
        for opt in q.get("options", []):
            mark = ' class="is-rec"' if q.get("rec") and opt.startswith(q["rec"]) else ""
            out.append(f'      <li{mark}>{esc(opt)}</li>')
        out.append('    </ul>')
        bits = []
        if q.get("rec"):
            bits.append(f'<dt>i\u2019d do</dt><dd>{esc(q["rec"])}</dd>')
        if q.get("blocks"):
            links = ", ".join(f'<a href="#{esc(b)}">{esc(b)}</a>' for b in q["blocks"])
            bits.append(f'<dt>unblocks</dt><dd>{links}</dd>')
        if q.get("who"):
            bits.append(f'<dt>who acts</dt><dd>{esc(q["who"])}</dd>')
        if bits:
            out.append('    <dl class="meta">' + "".join(bits) + '</dl>')
        out.append('  </article>')
    out.append('</section>')
    return "\n".join(out)


def render_map(data):
    """Connected components of the needs/gates/related graph, as chains."""
    items = {i["id"]: i for g in data["groups"] for i in g["items"]}
    edges, adj = [], {}
    for iid, item in items.items():
        links = item.get("links") or {}
        for tgt in links.get("gates", []):
            edges.append((iid, tgt, "gates"))
        for tgt in links.get("related", []):
            if not any(e[0] == tgt and e[1] == iid and e[2] == "related" for e in edges):
                edges.append((iid, tgt, "related"))
    for a, b, _ in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    seen, clusters = set(), []
    for node in sorted(adj):
        if node in seen:
            continue
        stack, comp = [node], []
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur); comp.append(cur)
            stack.extend(adj.get(cur, ()))
        clusters.append(sorted(comp))

    clusters.sort(key=len, reverse=True)
    out = ['<aside class="map">', '  <h2>How they connect</h2>',
           '  <p class="legend"><b>gates</b> must land first &nbsp;&middot;&nbsp; <b>with</b> same problem, no order</p>']
    for comp in clusters:
        hub = max(comp, key=lambda n: len(adj.get(n, ())))
        out.append('  <div class="cluster">')
        out.append(f'    <h3>{esc(hub.replace("-", " "))}</h3>')
        for a, b, kind in edges:
            if a not in comp:
                continue
            done = " is-done" if items.get(a, {}).get("status") == "done" else ""
            sev_a = items.get(a, {}).get("sev", "medium")
            sev_b = items.get(b, {}).get("sev", "medium")
            out.append(f'    <div class="edge k-{kind}{done}">')
            out.append(f'      <a class="n sev-{esc(sev_a)}" href="#{esc(a)}">{esc(a)}</a>')
            out.append(f'      <span class="rel">{esc(kind)}</span>')
            out.append(f'      <a class="n sev-{esc(sev_b)}" href="#{esc(b)}">{esc(b)}</a>')
            out.append('    </div>')
        out.append('  </div>')
    out.append('</aside>')
    return "\n".join(out)


def main():
    data = json.load(open(os.path.join(HERE, "items.json"), encoding="utf-8"))
    notes = load_notes()

    # Items a pending question is holding up sort to the top of their group and carry a marker.
    awaiting = {b for q in data.get("questions", []) for b in q.get("blocks", [])}
    for group in data["groups"]:
        for item in group["items"]:
            item["awaitingYou"] = item["id"] in awaiting
        group["items"].sort(key=lambda i: (not i.get("awaitingYou"), i.get("status") == "done"))

    all_items = [i for g in data["groups"] for i in g["items"]]
    open_count = sum(1 for i in all_items if i.get("status") != "done")
    live_count = sum(1 for i in all_items if i.get("sev") == "live" and i.get("status") != "done")
    done_count = sum(1 for i in all_items if i.get("status") == "done")
    blocked_count = sum(1 for i in all_items if i.get("status") == "blocked")
    note_count = sum(len(v) for v in notes.values())

    body = []
    for group in data["groups"]:
        body.append('<section class="group">')
        body.append(f'  <h2>{esc(group["name"])}</h2>')
        if group.get("blurb"):
            body.append(f'  <p class="blurb">{esc(group["blurb"])}</p>')
        for item in group["items"]:
            body.append(render_item(item, notes))
        body.append('</section>')

    sessions = "\n".join(
        f'      <tr><td><code>{esc(x["name"])}</code>'
        + (f' <span class="ref">[{esc(x["ref"])}]</span>' if x.get("ref") else "")
        + f'</td><td>{esc(x.get("role",""))}</td><td>{esc(x.get("holds",""))}</td></tr>'
        for x in data.get("sessions", []))

    questions = render_questions(data.get("questions", []))
    session_count = len(data.get("sessions", []))
    linkmap = render_map(data)

    worktrees = git_worktrees()
    if worktrees:
        rows = "\n".join(
            f'<tr><td>{esc(r)}</td><td><code>{esc(w)}</code></td><td><code>{esc(b)}</code></td>'
            f'<td>{esc(sess) if sess else "<span class=chk>&mdash;</span>"}</td></tr>'
            for r, w, b, sess in worktrees)
    else:
        rows = '<tr><td colspan="3" class="empty">No worktrees open.</td></tr>'

    page = TEMPLATE.format(
        board_title=esc(data.get("title", "Workboard")),
        board_sub=esc(data.get("subtitle",
                      "What is open, the evidence, who holds it, and what they have found.")),
        open_count=open_count, live_count=live_count, done_count=done_count,
        blocked_count=blocked_count, note_count=note_count,
        body="\n".join(body), worktrees=rows, sessions=sessions,
        worktree_count=len(worktrees), session_count=session_count,
        unclaimed_count=sum(1 for w in worktrees if not w[3]),
        questions=questions, linkmap=linkmap)

    with open(OUT, "w", encoding="utf-8") as handle:
        handle.write(page)
    print(f"wrote {OUT}  ({len(all_items)} items, {note_count} notes, {len(worktrees)} worktrees)")


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{board_title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Serif:wght@500;600&display=swap">
<style>
:root {{
  --ground: #f4f6f8;
  --surface: #ffffff;
  --surface-2: #eef1f5;
  --line: #d8dde5;
  --ink: #171c23;
  --ink-2: #4a5462;
  --ink-3: #77828f;
  --accent: #9a5a12;
  --live: #a8332b;
  --high: #9a5a12;
  --medium: #4a5462;
  --low: #77828f;
  --design: #3f5a8a;
  --done: #2c6e4c;
  --stripe: var(--medium);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ground: #0e1218;
    --surface: #151b23;
    --surface-2: #1b222c;
    --line: #2a3340;
    --ink: #e3e8ef;
    --ink-2: #a3aebd;
    --ink-3: #7b8797;
    --accent: #d59a4e;
    --live: #e0736a;
    --high: #d59a4e;
    --medium: #a3aebd;
    --low: #7b8797;
    --design: #8aa6d8;
    --done: #67b98d;
  }}
}}
:root[data-theme="dark"] {{
  --ground: #0e1218;
  --surface: #151b23;
  --surface-2: #1b222c;
  --line: #2a3340;
  --ink: #e3e8ef;
  --ink-2: #a3aebd;
  --ink-3: #7b8797;
  --accent: #d59a4e;
  --live: #e0736a;
  --high: #d59a4e;
  --medium: #a3aebd;
  --low: #7b8797;
  --design: #8aa6d8;
  --done: #67b98d;
}}

* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--ground);
  color: var(--ink);
  font-family: "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif;
  font-size: 15px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}}
.wrap {{ max-width: 132rem; margin: 0 auto; padding: 2rem 1.25rem 5rem; }}

.masthead {{ border-bottom: 2px solid var(--ink); padding-bottom: 1.25rem; margin-bottom: 2rem; }}
.masthead h1 {{
  font-family: "IBM Plex Serif", Georgia, serif;
  font-weight: 600; font-size: clamp(1.9rem, 4vw, 2.6rem);
  margin: 0 0 .3rem; letter-spacing: -.015em; text-wrap: balance;
}}
.masthead .sub {{ color: var(--ink-2); margin: 0; max-width: 62ch; }}

.tallies {{ display: flex; flex-wrap: wrap; gap: 0; margin: 1.75rem 0 2.5rem; border: 1px solid var(--line); border-radius: 3px; overflow: hidden; background: var(--surface); }}
.tally {{ flex: 1 1 7rem; padding: .85rem 1rem; border-right: 1px solid var(--line); }}
.tally:last-child {{ border-right: 0; }}
.tally b {{ display: block; font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 1.6rem; font-weight: 500; line-height: 1.1; font-variant-numeric: tabular-nums; }}
.tally span {{ font-size: .72rem; text-transform: uppercase; letter-spacing: .09em; color: var(--ink-3); }}
.tally.is-live b {{ color: var(--live); }}
.tally.is-done b {{ color: var(--done); }}

/* One column stacked; two once there is room for the map; three on ultrawide, where the
   questions get their own column instead of pushing the work items down. */
.context {{ display: flex; flex-wrap: wrap; gap: .5rem; margin: 0 0 1.5rem; }}
.context details {{ flex: 1 1 16rem; border: 1px solid var(--line); border-radius: 3px; background: var(--surface); }}
.context summary {{ cursor: pointer; padding: .5rem .75rem; font-size: .72rem; text-transform: uppercase; letter-spacing: .08em; color: var(--ink-3); }}
.context summary b {{ font-family: "IBM Plex Mono", ui-monospace, monospace; color: var(--ink); background: var(--surface-2); padding: 0 .3rem; border-radius: 2px; margin-left: .25rem; }}
.context details[open] {{ flex-basis: 100%; }}
.context details[open] summary {{ border-bottom: 1px solid var(--line); }}
.context .blurb {{ margin: .5rem .75rem; font-size: .82rem; color: var(--ink-2); }}
.context table {{ width: 100%; border-collapse: collapse; font-size: .82rem; }}
.context th, .context td {{ text-align: left; padding: .35rem .75rem; border-bottom: 1px solid var(--line); }}
.context th {{ font-size: .66rem; text-transform: uppercase; letter-spacing: .08em; color: var(--ink-3); position: sticky; top: 0; background: var(--surface-2); }}
.context tr:last-child td {{ border-bottom: 0; }}
.chk {{ color: var(--ink-3); }}
.unclaimed {{ font-family: "IBM Plex Mono", ui-monospace, monospace; color: var(--live); text-transform: none; letter-spacing: 0; margin-left: .4rem; }}
.context code {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: .78rem; }}

.cols {{ display: grid; grid-template-columns: minmax(0,1fr); gap: 1.5rem; align-items: start; }}
@media (min-width: 68rem) {{
  .cols {{ grid-template-columns: minmax(0,1fr) 17rem; }}
  .questions {{ grid-column: 1 / -1; }}
}}
@media (min-width: 104rem) {{
  .cols {{ grid-template-columns: 25rem minmax(0,1fr) 18rem; }}
  .questions {{ grid-column: 1; position: sticky; top: 1.25rem; max-height: calc(100vh - 2.5rem); overflow-y: auto; margin-bottom: 0; }}
  main {{ grid-column: 2; }}
  .map {{ grid-column: 3; }}
}}
@media (max-width: 68rem) {{ .map {{ position: static !important; max-height: none !important; }} }}

.questions {{ border: 1px solid var(--accent); border-radius: 3px; padding: 1.1rem 1.25rem; margin: 0 0 2.5rem; background: var(--surface); }}
.questions > h2 {{ font-family: "IBM Plex Serif", Georgia, serif; font-size: 1.15rem; margin: 0 0 .3rem; color: var(--accent); }}
.qcount {{ font-family: "IBM Plex Mono", ui-monospace, monospace; background: var(--accent); color: var(--surface); padding: 0 .35rem; border-radius: 2px; font-size: .85rem; }}
.q {{ border-top: 1px solid var(--line); padding-top: .85rem; margin-top: .85rem; }}
.q h3 {{ font-size: .97rem; margin: 0 0 .4rem; display: flex; gap: .5rem; align-items: baseline; text-wrap: balance; }}
.qnum {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: .78rem; color: var(--accent); border: 1px solid var(--accent); border-radius: 2px; padding: 0 .3rem; flex: none; }}
.opts {{ list-style: none; margin: .5rem 0; padding: 0; display: flex; flex-direction: column; gap: .25rem; }}
.opts li {{ font-size: .87rem; padding: .3rem .55rem; border: 1px solid var(--line); border-radius: 2px; color: var(--ink-2); }}
.opts li.is-rec {{ border-color: var(--accent); color: var(--ink); }}
.opts li.is-rec::after {{ content: " \2190 recommended"; color: var(--accent); font-size: .74rem; letter-spacing: .04em; }}

.map {{ position: sticky; top: 1.5rem; max-height: calc(100vh - 3rem); overflow-y: auto; font-size: .8rem; }}
.map > h2 {{ font-family: "IBM Plex Serif", Georgia, serif; font-size: 1rem; margin: 0 0 .2rem; padding-bottom: .35rem; border-bottom: 1px solid var(--line); }}
.legend {{ color: var(--ink-3); font-size: .72rem; margin: 0 0 1rem; }}
.cluster {{ margin-bottom: 1.15rem; }}
.cluster h3 {{ font-size: .7rem; text-transform: uppercase; letter-spacing: .09em; color: var(--ink-3); margin: 0 0 .4rem; font-weight: 600; }}
.edge {{ display: flex; flex-direction: column; gap: .1rem; margin-bottom: .5rem; padding-left: .5rem; border-left: 2px solid var(--line); }}
.edge.k-related {{ border-left-style: dotted; }}
.edge.is-done {{ opacity: .55; }}
.edge .n {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: .72rem; text-decoration: none; color: var(--ink-2); border-left: 2px solid var(--stripe); padding-left: .35rem; }}
.edge .n:hover {{ color: var(--ink); text-decoration: underline; }}
.edge .n.sev-live {{ --stripe: var(--live); }}
.edge .n.sev-high {{ --stripe: var(--high); }}
.edge .n.sev-design {{ --stripe: var(--design); }}
.edge .n.sev-done {{ --stripe: var(--done); }}
.edge .rel {{ font-size: .64rem; text-transform: uppercase; letter-spacing: .09em; color: var(--ink-3); padding-left: .35rem; }}
.k-gates .rel {{ color: var(--high); }}

.group {{ margin: 0 0 2.75rem; }}
.group > h2 {{
  font-family: "IBM Plex Serif", Georgia, serif;
  font-size: 1.15rem; font-weight: 600; margin: 0 0 .35rem;
  padding-bottom: .4rem; border-bottom: 1px solid var(--line);
}}
.group > .blurb {{ color: var(--ink-2); margin: .5rem 0 1.25rem; font-size: .92rem; }}

.item {{
  background: var(--surface);
  border: 1px solid var(--line);
  border-left: 3px solid var(--stripe);
  border-radius: 3px;
  padding: 1rem 1.15rem;
  margin-bottom: .85rem;
}}
.item.sev-live {{ --stripe: var(--live); }}
.item.sev-high {{ --stripe: var(--high); }}
.item.sev-design {{ --stripe: var(--design); }}
.item.sev-low {{ --stripe: var(--low); }}
.item.sev-done {{ --stripe: var(--done); opacity: .72; }}

.item-head {{ display: flex; align-items: baseline; gap: .6rem; flex-wrap: wrap; }}
.item-head h3 {{ font-size: 1rem; font-weight: 600; margin: 0; flex: 1 1 20rem; text-wrap: balance; }}
.slug {{
  display: inline-block; font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: .74rem; font-weight: 500; letter-spacing: .01em;
  color: var(--stripe); text-decoration: none; margin-bottom: .3rem;
}}
.slug::before {{ content: "#"; opacity: .5; }}
.slug:hover, .slug:focus-visible {{ text-decoration: underline; }}
:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
.chips {{ display: flex; gap: .35rem; }}
.chip {{ font-size: .68rem; text-transform: uppercase; letter-spacing: .07em; padding: .16rem .45rem; border-radius: 2px; white-space: nowrap; }}
.chip-sev {{ color: var(--stripe); border: 1px solid var(--stripe); }}
.chip-st {{ color: var(--ink-3); border: 1px solid var(--line); background: var(--surface-2); }}
.st-blocked .chip-st {{ color: var(--live); border-color: var(--live); }}
.st-assigned .chip-st {{ color: var(--design); border-color: var(--design); }}
.chip-you {{ color: var(--surface); background: var(--accent); border: 1px solid var(--accent); font-weight: 600; }}
.item[draggable="true"] {{ cursor: grab; }}
.item.dragging {{ opacity: .4; cursor: grabbing; }}
.item.drop-before {{ box-shadow: 0 -3px 0 var(--accent); }}
.item.drop-after {{ box-shadow: 0 3px 0 var(--accent); }}
.st-superseded {{ opacity: .68; }}
.st-superseded .chip-st {{ color: var(--low); border-color: var(--low); }}

.why {{ margin: .6rem 0 .7rem; color: var(--ink-2); }}

.evidence {{ list-style: none; margin: 0 0 .7rem; padding: 0; display: flex; flex-direction: column; gap: .18rem; }}
.evidence code {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: .78rem; color: var(--ink-3); word-break: break-word; }}

.meta {{ display: grid; grid-template-columns: auto 1fr; gap: .1rem .7rem; margin: 0 0 .8rem; font-size: .85rem; }}
.meta dt {{ font-size: .68rem; text-transform: uppercase; letter-spacing: .07em; color: var(--ink-3); padding-top: .2rem; }}
.meta dd {{ margin: 0; color: var(--ink); }}

.files {{ margin: 0 0 .75rem; display: flex; flex-direction: column; gap: .3rem; }}
.files details {{ border: 1px solid var(--line); border-radius: 2px; background: var(--surface-2); }}
.files summary {{ cursor: pointer; padding: .3rem .6rem; font-size: .72rem; text-transform: uppercase; letter-spacing: .08em; color: var(--ink-3); }}
.files summary b {{ font-family: "IBM Plex Mono", ui-monospace, monospace; color: var(--ink-2); }}
.files details[open] summary {{ border-bottom: 1px solid var(--line); }}
.files ul {{ list-style: none; margin: 0; padding: .45rem .6rem; display: flex; flex-direction: column; gap: .2rem; }}
.files code {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: .74rem; color: var(--ink-2); word-break: break-word; }}
.files .empty {{ padding: .45rem .6rem; }}
.project-first {{ margin: 0; padding: .4rem .6rem; font-size: .8rem; color: var(--ink); background: var(--surface-2); border-left: 2px solid var(--design); border-radius: 2px; }}

.log {{ border-top: 1px dashed var(--line); padding-top: .5rem; }}
.log summary {{ cursor: pointer; font-size: .68rem; text-transform: uppercase; letter-spacing: .09em; color: var(--ink-3); font-weight: 600; list-style-position: outside; }}
.log summary b {{ font-family: "IBM Plex Mono", ui-monospace, monospace; color: var(--ink-2); background: var(--surface-2); padding: 0 .3rem; border-radius: 2px; margin-left: .25rem; }}
.log summary:hover {{ color: var(--ink); }}
.log ol {{ margin: .5rem 0 0; padding-left: 1.1rem; display: flex; flex-direction: column; gap: .45rem; }}
.log li {{ font-size: .88rem; color: var(--ink-2); }}
.byline {{ display: block; font-size: .72rem; color: var(--ink-3); }}
.byline b {{ color: var(--accent); font-weight: 600; }}
.byline .where {{ font-family: "IBM Plex Mono", ui-monospace, monospace; }}
.byline time {{ font-variant-numeric: tabular-nums; }}
.empty {{ color: var(--ink-3); font-size: .85rem; margin: 0; font-style: italic; }}

.tracking table {{ width: 100%; border-collapse: collapse; font-size: .85rem; background: var(--surface); border: 1px solid var(--line); }}
.tracking th, .tracking td {{ text-align: left; padding: .45rem .7rem; border-bottom: 1px solid var(--line); }}
.tracking th {{ font-size: .68rem; text-transform: uppercase; letter-spacing: .08em; color: var(--ink-3); }}
.tracking tr:last-child td {{ border-bottom: 0; }}
.ref {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: .72rem; color: var(--ink-3); }}
.tracking code {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: .8rem; }}
.scroller {{ overflow-x: auto; max-height: 22rem; overflow-y: auto; border-radius: 3px; }}
.tracking thead th {{ position: sticky; top: 0; background: var(--surface-2); }}

.howto {{ background: var(--surface-2); border: 1px solid var(--line); border-radius: 3px; padding: 1rem 1.15rem; font-size: .88rem; }}
.howto h2 {{ font-family: "IBM Plex Serif", Georgia, serif; font-size: 1rem; margin: 0 0 .5rem; }}
.howto pre {{ overflow-x: auto; background: var(--surface); border: 1px solid var(--line); padding: .6rem .7rem; border-radius: 2px; font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: .76rem; margin: .5rem 0 0; }}
.howto p {{ margin: .4rem 0; color: var(--ink-2); }}
</style>
</head>
<body>
<div class="wrap">

<header class="masthead">
  <h1>{board_title}</h1>
  <p class="sub">{board_sub}</p>
</header>

<div class="tallies">
  <div class="tally is-live"><b>{live_count}</b><span>live bugs</span></div>
  <div class="tally"><b>{open_count}</b><span>open</span></div>
  <div class="tally"><b>{blocked_count}</b><span>blocked</span></div>
  <div class="tally is-done"><b>{done_count}</b><span>shipped</span></div>
  <div class="tally"><b>{note_count}</b><span>notes</span></div>
</div>

<div class="context">
  <details>
    <summary>Open worktrees <b>{worktree_count}</b> <span class="unclaimed">{unclaimed_count} unclaimed</span></summary>
    <p class="blurb">Read live from every repo in the workspace at build time.</p>
    <div class="scroller">
    <table>
      <thead><tr><th>repo</th><th>worktree</th><th>branch</th><th>session</th></tr></thead>
      <tbody>
{worktrees}
      </tbody>
    </table>
    </div>
  </details>

  <details>
    <summary>Sessions <b>{session_count}</b></summary>
    <div class="scroller">
    <table>
      <thead><tr><th>session</th><th>role</th><th>holds</th></tr></thead>
      <tbody>
{sessions}
      </tbody>
    </table>
    </div>
  </details>

  <details>
    <summary>YouTrack <b>0</b></summary>
    <p class="blurb">No issues cut yet. CLAUDE.md requires one per item of implementation work &mdash; these need creating before the work lands.</p>
  </details>
</div>

<div class="cols">

{questions}

<main>

{body}

</main>

{linkmap}

</div>

<section class="howto">
  <h2>How a worker files a note</h2>
  <p>Nobody edits <code>WORKBOARD.html</code>. Append one line, rebuild, done &mdash; appends do not collide.</p>
  <pre>echo '{{"item":"swallowed-factory-failure","who":"agent-name","session":"abc123","worktree":"registration-defects-rst","ts":"2026-08-21","note":"what you found"}}' \\
  &gt;&gt; .workboard/notes.jsonl
python3 .workboard/build.py</pre>
  <p>The item name is the <code>#slug</code> at the top of each card. <code>items.json</code> holds status and severity; the board owner maintains it.</p>
</section>

</div>
<script>
// Drag a card to reorder it within its group. Order is per-browser and survives a rebuild,
// because what is stored is the list of ids, not the markup. Storage can be unavailable on
// file:// in some browsers - when it is, dragging still works for the session and simply
// does not persist, which is why every access is guarded.
(function () {{
  var KEY = "workboard.order.v1";

  function readOrder() {{
    try {{ return JSON.parse(localStorage.getItem(KEY)) || {{}}; }}
    catch (e) {{ return {{}}; }}
  }}
  function writeOrder(order) {{
    try {{ localStorage.setItem(KEY, JSON.stringify(order)); }} catch (e) {{ /* not persisted */ }}
  }}

  var groups = Array.prototype.slice.call(document.querySelectorAll("main .group"));

  // Restore a saved arrangement. Unknown ids are ignored and new items keep their built-in
  // position, so a rebuild that adds an item does not strand it.
  var saved = readOrder();
  groups.forEach(function (group, gi) {{
    var ids = saved["g" + gi];
    if (!ids) return;
    ids.forEach(function (id) {{
      var card = group.querySelector('.item[data-id="' + CSS.escape(id) + '"]');
      if (card) group.appendChild(card);
    }});
  }});

  function save() {{
    var order = {{}};
    groups.forEach(function (group, gi) {{
      order["g" + gi] = Array.prototype.map.call(
        group.querySelectorAll(".item"), function (c) {{ return c.dataset.id; }});
    }});
    writeOrder(order);
  }}

  var dragged = null;

  document.addEventListener("dragstart", function (e) {{
    var card = e.target.closest && e.target.closest(".item");
    if (!card) return;
    dragged = card;
    card.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    try {{ e.dataTransfer.setData("text/plain", card.dataset.id); }} catch (err) {{}}
  }});

  document.addEventListener("dragend", function () {{
    if (dragged) dragged.classList.remove("dragging");
    document.querySelectorAll(".drop-before, .drop-after").forEach(function (c) {{
      c.classList.remove("drop-before", "drop-after");
    }});
    dragged = null;
  }});

  document.addEventListener("dragover", function (e) {{
    if (!dragged) return;
    var over = e.target.closest && e.target.closest(".item");
    if (!over || over === dragged) return;
    if (over.parentElement !== dragged.parentElement) return;   // reorder within a group only
    e.preventDefault();
    var box = over.getBoundingClientRect();
    var before = e.clientY < box.top + box.height / 2;
    over.classList.toggle("drop-before", before);
    over.classList.toggle("drop-after", !before);
  }});

  document.addEventListener("dragleave", function (e) {{
    var over = e.target.closest && e.target.closest(".item");
    if (over) over.classList.remove("drop-before", "drop-after");
  }});

  document.addEventListener("drop", function (e) {{
    if (!dragged) return;
    var over = e.target.closest && e.target.closest(".item");
    if (!over || over === dragged || over.parentElement !== dragged.parentElement) return;
    e.preventDefault();
    var box = over.getBoundingClientRect();
    if (e.clientY < box.top + box.height / 2) over.parentElement.insertBefore(dragged, over);
    else over.parentElement.insertBefore(dragged, over.nextSibling);
    over.classList.remove("drop-before", "drop-after");
    save();
  }});
}})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
