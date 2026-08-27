#!/usr/bin/env python3
"""Build a portable, self-contained workboard page.

Differs from build.py in two ways that matter for moving it somewhere else:

  * no <!doctype>/<html>/<head> wrapper -- the Artifact host supplies that, and a page that
    brings its own renders nested and broken.
  * no shell-out to git. An artifact cannot run git wherever it lands, so worktrees come from
    an optional snapshot file and are labelled as of a moment rather than pretended to be live.

Everything else is inlined already: styles, script, data. The only external reference is
Google Fonts, which is the one host the Artifact CSP admits.

    python3 build-artifact.py <board-dir> [-o out.html]

<board-dir> holds .workboard/items.json and .workboard/notes.jsonl.
"""
import argparse
import html
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def find_template():
    """The template is language-neutral and lives in shared/, but this script has moved
    between layouts. Try the places it legitimately sits, then say what was tried."""
    candidates = [
        os.path.join(HERE, "template.html"),                                  # beside the script
        os.path.join(HERE, "..", "..", "shared", "board", "template.html"),   # public/<lang>/board
        os.path.join(HERE, "..", "shared", "board", "template.html"),
    ]
    for c in candidates:
        c = os.path.normpath(c)
        if os.path.isfile(c):
            return c
    raise SystemExit("cannot find template.html. Tried:\n  "
                     + "\n  ".join(os.path.normpath(c) for c in candidates))

SEV = {"live": "live bug", "high": "high", "medium": "medium", "low": "low",
       "design": "decision", "done": "shipped"}


def esc(v):
    return html.escape("" if v is None else str(v), quote=True)


def load(board):
    wb = os.path.join(board, ".workboard")
    with open(os.path.join(wb, "items.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    notes = {}
    path = os.path.join(wb, "notes.jsonl")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    # Why loud: a silently dropped note is a worker who believes it reported
                    # and is invisible. Name the line instead of hiding it.
                    raise SystemExit(f"notes.jsonl line {n} is not valid JSON: {exc}")
                notes.setdefault(row.get("item", "?"), []).append(row)
    trees = []
    snap = os.path.join(wb, "worktrees-snapshot.json")
    if os.path.exists(snap):
        with open(snap, encoding="utf-8") as fh:
            trees = json.load(fh)
    return data, notes, trees


def items_of(data):
    return [i for g in data.get("groups", []) for i in g.get("items", [])]


def render(data, notes, trees, stamp):
    all_items = items_of(data)
    awaiting = {b for q in data.get("questions", []) for b in q.get("blocks", [])}
    for item in all_items:
        item["_you"] = item.get("id") in awaiting

    def tally(label, value, cls=""):
        return (f'<div class="tally {cls}"><b>{value}</b><span>{esc(label)}</span></div>')

    tallies = "".join([
        tally("open", sum(1 for i in all_items if i.get("status") != "done")),
        tally("live", sum(1 for i in all_items if i.get("sev") == "live"
                          and i.get("status") != "done"), "is-live"),
        tally("blocked", sum(1 for i in all_items if i.get("status") == "blocked")),
        tally("awaiting you", len(awaiting), "is-you"),
        tally("questions", len(data.get("questions", []))),
        tally("notes", sum(len(v) for v in notes.values())),
        tally("shipped", sum(1 for i in all_items if i.get("status") == "done")),
    ])

    # ---- questions
    qs = []
    for n, q in enumerate(data.get("questions", []), 1):
        opts = "".join(
            f'<li class="{"is-rec" if q.get("rec") and o.startswith(q["rec"]) else ""}">{esc(o)}</li>'
            for o in q.get("options", []))
        meta = ""
        if q.get("rec"):
            meta += f"<dt>i'd do</dt><dd>{esc(q['rec'])}</dd>"
        if q.get("blocks"):
            meta += ("<dt>unblocks</dt><dd>"
                     + ", ".join(f'<a class="n" href="#{esc(b)}">{esc(b)}</a>'
                                 for b in q["blocks"]) + "</dd>")
        if q.get("who"):
            meta += f"<dt>who acts</dt><dd>{esc(q['who'])}</dd>"
        qs.append(f'<article class="q"><h3>{n}. {esc(q["q"])}</h3>'
                  f'<p class="why">{esc(q.get("why",""))}</p>'
                  f'<ul class="opts">{opts}</ul>'
                  + (f'<dl class="meta">{meta}</dl>' if meta else "") + "</article>")
    questions = (f'<section id="questions"><h2 class="grp">For you to answer '
                 f'<span class="chip chip-you">{len(qs)}</span></h2>'
                 f'<p class="blurb">Nothing here is blocked on work. Each one is a decision only '
                 f'you can make, with what I would do and why.</p>{"".join(qs)}</section>'
                 ) if qs else ""

    # ---- items
    def files(item, key, label):
        lst = item.get(key) or []
        if not lst:
            return ""
        return (f'<details data-k="{esc(item["id"])}-{key}"><summary>{label} <b>{len(lst)}</b>'
                f'</summary><ul>' + "".join(f"<li>{esc(f)}</li>" for f in lst) + "</ul></details>")

    def render_item(item):
        sev, status = item.get("sev", "medium"), item.get("status", "open")
        chips = (f'<span class="chip">{esc(SEV.get(sev, sev))}</span>'
                 f'<span class="chip">{esc(status)}</span>')
        if item["_you"]:
            chips += '<span class="chip chip-you">awaiting you</span>'
        ev = ("".join(f"<li>{esc(e)}</li>" for e in item.get("evidence", [])))
        meta = ""
        for k in ("fix", "repo", "owner"):
            if item.get(k):
                meta += f"<dt>{k}</dt><dd>{esc(item[k])}</dd>"
        log = notes.get(item["id"], [])
        entries = "".join(
            f'<li><span class="byline">{esc(e.get("who","?"))}'
            + (f' · {esc(e.get("worktree") or e.get("session"))}'
               if (e.get("worktree") or e.get("session")) else "")
            + (f' · {esc(e["ts"])}' if e.get("ts") else "")
            + (f' · {esc(e["kind"])}' if e.get("kind") else "")
            + f'</span>{esc(e.get("note",""))}</li>' for e in log)
        return (
            f'<article class="item sev-{esc(sev)} st-{esc(status)}" id="{esc(item["id"])}">'
            f'<a class="slug" href="#{esc(item["id"])}">#{esc(item["id"])}</a>'
            f'<h3>{esc(item["title"])}<span class="chips">{chips}</span></h3>'
            f'<p class="why">{esc(item.get("why",""))}</p>'
            + (f'<ul class="ev">{ev}</ul>' if ev else "")
            + (f'<dl class="meta">{meta}</dl>' if meta else "")
            + ('<p class="project-first">Project the impact BEFORE opening files — list every '
               'file you expect to create, delete or change, post it as a note, then work.</p>'
               if item.get("projectFirst") else "")
            + files(item, "expected", "expected files") + files(item, "actual", "actual changes")
            + f'<details class="log" data-k="{esc(item["id"])}-notes">'
              f'<summary>notes <b>{len(log)}</b></summary>'
            + (f"<ol>{entries}</ol>" if log else '<p class="empty">No notes yet.</p>')
            + "</details></article>")

    body = []
    for g in data.get("groups", []):
        rows = sorted(g.get("items", []),
                      key=lambda i: (not i["_you"], i.get("status") == "done"))
        body.append(f'<h2 class="grp">{esc(g["name"])}</h2>')
        if g.get("blurb"):
            body.append(f'<p class="blurb">{esc(g["blurb"])}</p>')
        body.extend(render_item(i) for i in rows)

    # ---- map
    byid = {i["id"]: i for i in all_items}
    edges, adj = [], {}
    for iid, item in byid.items():
        links = item.get("links") or {}
        for t in links.get("gates", []):
            edges.append((iid, t, "gates"))
        for t in links.get("related", []):
            if not any(e[0] == t and e[1] == iid and e[2] == "with" for e in edges):
                edges.append((iid, t, "with"))
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
            seen.add(cur)
            comp.append(cur)
            stack.extend(adj.get(cur, ()))
        clusters.append(sorted(comp))
    # explicit tiebreak so the PowerShell port can produce identical bytes
    clusters.sort(key=lambda c: (-len(c), c[0]))
    if edges:
        parts = ['<aside class="map"><h2 class="grp">How they connect</h2>',
                 '<p class="legend"><b>gates</b> must land first · <b>with</b> same problem, '
                 'no order</p>']
        for comp in clusters:
            hub = sorted(comp, key=lambda n: (-len(adj.get(n, ())), n))[0]
            parts.append(f'<div class="cluster"><h3>{esc(hub.replace("-", " "))}</h3>')
            for a, b, kind in edges:
                if a not in comp:
                    continue
                parts.append(f'<div class="edge"><a class="n" href="#{esc(a)}">{esc(a)}</a>'
                             f'<span class="rel">{esc(kind)}</span>'
                             f'<a class="n" href="#{esc(b)}">{esc(b)}</a></div>')
            parts.append("</div>")
        parts.append("</aside>")
        mapping = "".join(parts)
    else:
        mapping = ""

    # ---- panels
    panels = []
    sess = data.get("sessions", [])
    if sess:
        rows = "".join(f'<tr><td>{esc(s.get("name"))}'
                       + (f' [{esc(s["ref"])}]' if s.get("ref") and s["ref"] != "—" else "")
                       + f'</td><td>{esc(s.get("role",""))}</td>'
                         f'<td>{esc(s.get("holds",""))}</td></tr>' for s in sess)
        panels.append(f'<details class="panel" data-k="sessions"><summary>Sessions '
                      f'<b>{len(sess)}</b></summary><table><tr><th>session</th><th>role</th>'
                      f'<th>holds</th></tr>{rows}</table></details>')
    if trees:
        rows = "".join(f'<tr><td>{esc(t.get("repo"))}</td><td>{esc(t.get("name"))}</td>'
                       f'<td>{esc(t.get("branch","-"))}</td>'
                       f'<td>{esc(t.get("owner") or "—")}</td></tr>' for t in trees)
        panels.append(f'<details class="panel" data-k="worktrees"><summary>Worktrees '
                      f'<b>{len(trees)}</b> — snapshot, not live</summary><table>'
                      f'<tr><th>repo</th><th>worktree</th><th>branch</th><th>owner</th></tr>'
                      f'{rows}</table></details>')

    footer = (f'{len(all_items)} items · {sum(len(v) for v in notes.values())} notes · '
              f'{len(data.get("questions", []))} open questions · built {esc(stamp)}<br>'
              'Regenerate and republish to the same URL to update in place. '
              'Worktrees are a snapshot: an artifact cannot run git.')

    with open(find_template(), encoding="utf-8") as fh:
        tpl = fh.read()
    for token, value in (("TITLE", esc(data.get("title", "Workboard"))),
                         ("SUBTITLE", esc(data.get("subtitle",
                          "What is open, the evidence, who holds it, and what they have found."))),
                         ("TALLIES", tallies), ("PANELS", "".join(panels)),
                         ("QUESTIONS", questions), ("BODY", "".join(body)),
                         ("MAP", mapping), ("FOOTER", footer)):
        tpl = tpl.replace("{{" + token + "}}", value)
    return tpl


def main():
    ap = argparse.ArgumentParser(description="Build a portable workboard page.")
    ap.add_argument("board", nargs="?", default=".", help="folder holding .workboard/")
    ap.add_argument("-o", "--out", help="output file (default <board>/workboard-artifact.html)")
    ap.add_argument("--stamp", default="", help="build stamp; blank uses the current date")
    args = ap.parse_args()

    board = os.path.abspath(args.board)
    if not os.path.isdir(os.path.join(board, ".workboard")):
        sys.exit(f"no .workboard/ in {board}")
    stamp = args.stamp
    if not stamp:
        from datetime import datetime
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    data, notes, trees = load(board)
    out = args.out or os.path.join(board, "workboard-artifact.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(render(data, notes, trees, stamp))
    print(f"wrote {out}")
    print(f"  {len(items_of(data))} items · {sum(len(v) for v in notes.values())} notes · "
          f"{len(data.get('questions', []))} questions · {len(trees)} worktrees")


if __name__ == "__main__":
    main()
