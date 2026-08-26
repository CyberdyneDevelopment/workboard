'use strict';

const SEV = { live: 'live bug', high: 'high', medium: 'medium', low: 'low', design: 'decision', done: 'shipped' };

function esc(v) {
  return String(v === undefined || v === null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// `repo :: some/Path.cs:78`  |  `some/Path.cs:78`  |  `some/Path.cs`
// Why a single pass: two passes double-linkify the second half of a `repo :: path` token.
const LOC = new RegExp(
  '(?:([A-Za-z0-9][A-Za-z0-9._-]*)\\s*::\\s*)?' +      // optional repo prefix
  '((?:[A-Za-z0-9._-]+\\/)*[A-Za-z0-9._-]+\\.[A-Za-z]{1,10})' + // path with an extension
  '(?::(\\d+))?',                                       // optional :line
  'g');

/** Escape, then turn anything that looks like a file location into a clickable link. */
function linkify(text) {
  const raw = String(text === undefined || text === null ? '' : text);
  let out = '', last = 0, m;
  LOC.lastIndex = 0;
  while ((m = LOC.exec(raw)) !== null) {
    const [whole, repo, path, line] = m;
    // A bare word with a dot but no slash and no line is prose ("build.py" is fine,
    // "e.g" is not) - require either a slash, a line number, or a known code extension.
    const looksReal = path.includes('/') || line || /\.(cs|py|js|ts|json|jsonl|md|razor|csproj|slnx|yml|yaml|sh|html|css|sql|props|targets)$/i.test(path);
    if (!looksReal) continue;
    out += esc(raw.slice(last, m.index));
    const loc = JSON.stringify({ repo: repo || null, path, line: line ? Number(line) : null });
    out += `<a class="floc" href="#" data-loc='${esc(loc)}' title="Open ${esc(path)}">${esc(whole)}</a>`;
    last = m.index + whole.length;
  }
  return out + esc(raw.slice(last));
}

const CSS = `
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0 0 2rem;
  font-family: var(--vscode-font-family); font-size: var(--vscode-font-size);
  color: var(--vscode-foreground); background: var(--vscode-editor-background);
}
a { color: var(--vscode-textLink-foreground); text-decoration: none; }
a:hover { text-decoration: underline; }
a.floc {
  font-family: var(--vscode-editor-font-family); font-size: .92em;
  color: var(--vscode-textLink-foreground); cursor: pointer;
  border-bottom: 1px dotted var(--vscode-textLink-foreground);
}
a.floc:hover { background: var(--vscode-editor-selectionHighlightBackground); }
code, pre { font-family: var(--vscode-editor-font-family); font-size: .92em; }
.bar {
  position: sticky; top: 0; z-index: 5; display: flex; gap: .75rem; align-items: baseline;
  padding: .5rem .9rem; background: var(--vscode-sideBar-background);
  border-bottom: 1px solid var(--vscode-panel-border); flex-wrap: wrap;
}
.bar b { font-size: .95rem; } .bar .t { color: var(--vscode-descriptionForeground); font-size: .82rem; }
.wrap { padding: .75rem .9rem; max-width: 100%; }
.tallies { display: flex; flex-wrap: wrap; gap: .1rem 1.4rem; padding: .55rem .9rem .5rem;
           border-bottom: 1px solid var(--vscode-panel-border);
           background: var(--vscode-sideBar-background); position: sticky; top: 2rem; z-index: 4; }
.tally { display: flex; align-items: baseline; gap: .32rem; }
.tally b { font-size: 1.05rem; font-variant-numeric: tabular-nums; }
.tally span { font-size: .68rem; text-transform: uppercase; letter-spacing: .06em;
              color: var(--vscode-descriptionForeground); }
.tally.is-live b { color: var(--vscode-editorError-foreground); }
.tally.is-blocked b, .tally.is-you b { color: var(--vscode-editorWarning-foreground); }
h2 { font-size: .78rem; text-transform: uppercase; letter-spacing: .08em;
     color: var(--vscode-descriptionForeground); margin: 1.4rem 0 .1rem; font-weight: 600; }
h2:first-child { margin-top: .2rem; }
.blurb { color: var(--vscode-descriptionForeground); font-size: .85rem; margin: .25rem 0 .6rem; }
.item {
  border: 1px solid var(--vscode-panel-border); border-left-width: 3px;
  border-radius: 3px; padding: .6rem .7rem; margin: .5rem 0;
  background: var(--vscode-editorWidget-background);
}
.item.sev-live { border-left-color: var(--vscode-editorError-foreground); }
.item.sev-high { border-left-color: var(--vscode-editorWarning-foreground); }
.item.sev-design { border-left-color: var(--vscode-textLink-foreground); }
.item.sev-done, .item.st-done { opacity: .62; }
.slug { font-family: var(--vscode-editor-font-family); font-size: .78rem;
        color: var(--vscode-descriptionForeground); }
.item h3 { margin: .15rem 0 .3rem; font-size: .95rem; font-weight: 600; }
.chips { display: inline-flex; gap: .3rem; margin-left: .4rem; vertical-align: middle; }
.chip { font-size: .66rem; text-transform: uppercase; letter-spacing: .05em;
        padding: .05rem .35rem; border-radius: 2px;
        background: var(--vscode-badge-background); color: var(--vscode-badge-foreground); }
.chip-you { background: var(--vscode-inputValidation-warningBackground);
            color: var(--vscode-editorWarning-foreground); }
.why { margin: .3rem 0; line-height: 1.5; }
ul.ev { margin: .4rem 0; padding-left: 1.1rem; }
ul.ev li { margin: .12rem 0; }
dl.meta { display: grid; grid-template-columns: max-content 1fr; gap: .1rem .6rem;
          margin: .4rem 0 .2rem; font-size: .85rem; }
dl.meta dt { color: var(--vscode-descriptionForeground); text-transform: uppercase;
             font-size: .66rem; letter-spacing: .05em; padding-top: .18rem; }
details { margin: .35rem 0; }
summary { cursor: pointer; font-size: .74rem; text-transform: uppercase; letter-spacing: .06em;
          color: var(--vscode-descriptionForeground); }
details ul, details ol { margin: .35rem 0; padding-left: 1.1rem; }
.byline { display: block; font-size: .72rem; color: var(--vscode-descriptionForeground); }
.q { border: 1px solid var(--vscode-panel-border); border-left: 3px solid var(--vscode-editorWarning-foreground);
     border-radius: 3px; padding: .55rem .65rem; margin: .5rem 0;
     background: var(--vscode-editorWidget-background); }
.q h3 { font-size: .9rem; margin: 0 0 .3rem; }
.opts { list-style: none; margin: .4rem 0; padding: 0; }
.opts li { padding: .18rem .4rem; border-left: 2px solid transparent; font-size: .85rem; }
.opts li.rec { border-left-color: var(--vscode-editorWarning-foreground);
               background: var(--vscode-editorWidget-background); font-weight: 600; }
.edge { display: flex; align-items: center; gap: .3rem; flex-wrap: wrap; margin: .18rem 0; font-size: .8rem; }
.edge .rel { color: var(--vscode-descriptionForeground); font-size: .68rem;
             text-transform: uppercase; letter-spacing: .05em; }
.cluster { margin: .5rem 0 .8rem; }
.cluster h3 { font-size: .78rem; margin: 0 0 .25rem; text-transform: capitalize; }
table { border-collapse: collapse; width: 100%; font-size: .82rem; }
th, td { text-align: left; padding: .22rem .4rem; border-bottom: 1px solid var(--vscode-panel-border);
         vertical-align: top; }
th { font-size: .66rem; text-transform: uppercase; letter-spacing: .06em;
     color: var(--vscode-descriptionForeground); }
pre.out { white-space: pre-wrap; word-break: break-word; margin: 0; padding: .5rem .6rem;
          background: var(--vscode-textCodeBlock-background); border-radius: 3px; line-height: 1.45; }
.empty { color: var(--vscode-descriptionForeground); font-style: italic; padding: .6rem .9rem; }
`;

function shell({ nonce, csp, body, title }) {
  return `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; style-src ${csp} 'unsafe-inline'; script-src 'nonce-${nonce}';">
<title>${esc(title || 'Workboard')}</title><style>${CSS}</style></head>
<body>${body}
<script nonce="${nonce}">
const vscode = acquireVsCodeApi();
document.addEventListener('click', (e) => {
  const a = e.target.closest('a.floc');
  if (a) { e.preventDefault(); vscode.postMessage({ type: 'open', loc: JSON.parse(a.dataset.loc) }); return; }
  const j = e.target.closest('a.jump');
  if (j) { e.preventDefault();
    const el = document.getElementById(j.dataset.target);
    if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'center' });
              el.style.outline = '2px solid var(--vscode-focusBorder)';
              setTimeout(() => { el.style.outline = ''; }, 1200); }
    else vscode.postMessage({ type: 'reveal', id: j.dataset.target });
  }
});
window.addEventListener('message', (e) => {
  if (e.data && e.data.type === 'scrollTo') {
    const el = document.getElementById(e.data.id);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.style.outline = '2px solid var(--vscode-focusBorder)';
    setTimeout(() => { el.style.outline = ''; }, 1200);
  }
});
// Keep <details> open/closed across refreshes - a re-render must not collapse what you opened.
const KEY='wb-open';
const open=new Set(JSON.parse(vscode.getState()?.open || '[]'));
document.querySelectorAll('details[data-k]').forEach(d=>{
  if(open.has(d.dataset.k)) d.open=true;
  d.addEventListener('toggle',()=>{ d.open?open.add(d.dataset.k):open.delete(d.dataset.k);
    vscode.setState({open:JSON.stringify([...open])}); });
});
</script></body></html>`;
}

const allItems = (d) => (d.groups || []).flatMap((g) => g.items || []);

function awaitingSet(d) {
  return new Set((d.questions || []).flatMap((q) => q.blocks || []));
}

function renderItem(it, notes, awaiting) {
  const ns = notes[it.id] || [];
  const chips = [`<span class="chip">${esc(SEV[it.sev] || it.sev || '')}</span>`,
                 `<span class="chip">${esc(it.status || 'open')}</span>`];
  if (awaiting.has(it.id)) chips.push('<span class="chip chip-you">awaiting you</span>');
  const meta = [];
  if (it.fix) meta.push(['fix', linkify(it.fix)]);
  if (it.repo) meta.push(['repo', esc(it.repo)]);
  if (it.owner) meta.push(['owner', esc(it.owner)]);
  const files = (key, label) => {
    const list = it[key] || [];
    if (!list.length) return '';
    return `<details data-k="${esc(it.id + key)}"><summary>${label} <b>${list.length}</b></summary><ul class="ev">` +
      list.map((f) => `<li>${linkify(f)}</li>`).join('') + '</ul></details>';
  };
  return `<article class="item sev-${esc(it.sev || 'medium')} st-${esc(it.status || 'open')}" id="i-${esc(it.id)}">
  <div class="slug">#${esc(it.id)}</div>
  <h3>${esc(it.title)}<span class="chips">${chips.join('')}</span></h3>
  <div class="why">${linkify(it.why)}</div>
  ${(it.evidence || []).length ? '<ul class="ev">' + it.evidence.map((e) => `<li>${linkify(e)}</li>`).join('') + '</ul>' : ''}
  ${meta.length ? '<dl class="meta">' + meta.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('') + '</dl>' : ''}
  ${it.projectFirst ? '<div class="blurb">Project the impact BEFORE opening files — list every file you expect to change, post it as a note, then work.</div>' : ''}
  ${files('expected', 'expected files')}${files('actual', 'actual changes')}
  <details data-k="${esc(it.id)}notes"><summary>notes <b>${ns.length}</b></summary>${
    ns.length ? '<ol>' + ns.map((n) => `<li><span class="byline">${esc(n.who || '?')}${
      n.worktree || n.session ? ' · ' + esc(n.worktree || n.session) : ''}${
      n.ts ? ' · ' + esc(n.ts) : ''}${n.kind ? ' · ' + esc(n.kind) : ''}</span>${linkify(n.note)}</li>`).join('') + '</ol>'
      : '<p class="empty">No notes yet.</p>'}</details>
</article>`;
}

function renderBoard(d, notes, meta) {
  const awaiting = awaitingSet(d);
  const items = allItems(d);
  const n = {
    open: items.filter((i) => i.status !== 'done').length,
    live: items.filter((i) => i.sev === 'live' && i.status !== 'done').length,
    blocked: items.filter((i) => i.status === 'blocked').length,
    done: items.filter((i) => i.status === 'done').length,
    notes: Object.values(notes).reduce((a, b) => a + b.length, 0),
    questions: (d.questions || []).length,
  };
  const tally = (label, value, cls) =>
    `<div class="tally ${cls || ''}"><b>${value}</b><span>${esc(label)}</span></div>`;
  const body = `<div class="bar"><b>${esc(d.title || meta.name)}</b>
      <span class="t">${esc(meta.name)}</span></div>
    <div class="tallies">
      ${tally('open', n.open)}
      ${tally('live', n.live, n.live ? 'is-live' : '')}
      ${tally('blocked', n.blocked, n.blocked ? 'is-blocked' : '')}
      ${tally('awaiting you', awaiting.size, awaiting.size ? 'is-you' : '')}
      ${tally('questions', n.questions)}
      ${tally('notes', n.notes)}
      ${tally('shipped', n.done)}
    </div>
  <div class="wrap">` + (d.groups || []).map((g) => {
    const sorted = (g.items || []).slice().sort((a, b) =>
      (awaiting.has(b.id) ? 1 : 0) - (awaiting.has(a.id) ? 1 : 0) ||
      (a.status === 'done' ? 1 : 0) - (b.status === 'done' ? 1 : 0));
    return `<h2>${esc(g.name)}</h2>${g.blurb ? `<p class="blurb">${esc(g.blurb)}</p>` : ''}` +
      sorted.map((it) => renderItem(it, notes, awaiting)).join('');
  }).join('') + '</div>';
  return body;
}

function renderQuestions(d) {
  const qs = d.questions || [];
  if (!qs.length) return '<p class="empty">No open questions.</p>';
  return '<div class="wrap">' + qs.map((q, n) => `<article class="q">
    <h3>${n + 1}. ${esc(q.q)}</h3>
    <div class="why">${linkify(q.why)}</div>
    <ul class="opts">${(q.options || []).map((o) =>
      `<li class="${q.rec && o.startsWith(q.rec) ? 'rec' : ''}">${esc(o)}</li>`).join('')}</ul>
    <dl class="meta">${q.rec ? `<dt>i'd do</dt><dd>${esc(q.rec)}</dd>` : ''}
      ${(q.blocks || []).length ? `<dt>unblocks</dt><dd>${q.blocks.map((b) =>
        `<a class="jump" href="#" data-target="i-${esc(b)}">${esc(b)}</a>`).join(', ')}</dd>` : ''}
      ${q.who ? `<dt>who acts</dt><dd>${esc(q.who)}</dd>` : ''}</dl></article>`).join('') + '</div>';
}

function renderMap(d) {
  const items = {}; allItems(d).forEach((i) => { items[i.id] = i; });
  const edges = [], adj = {};
  for (const [id, it] of Object.entries(items)) {
    const l = it.links || {};
    (l.gates || []).forEach((t) => edges.push([id, t, 'gates']));
    (l.related || []).forEach((t) => {
      if (!edges.some((e) => e[0] === t && e[1] === id && e[2] === 'with')) edges.push([id, t, 'with']);
    });
  }
  edges.forEach(([a, b]) => { (adj[a] = adj[a] || new Set()).add(b); (adj[b] = adj[b] || new Set()).add(a); });
  if (!edges.length) return '<p class="empty">Nothing linked yet.</p>';
  const seen = new Set(), clusters = [];
  Object.keys(adj).sort().forEach((n) => {
    if (seen.has(n)) return;
    const stack = [n], comp = [];
    while (stack.length) { const c = stack.pop(); if (seen.has(c)) continue;
      seen.add(c); comp.push(c); (adj[c] || []).forEach((x) => stack.push(x)); }
    clusters.push(comp.sort());
  });
  clusters.sort((a, b) => b.length - a.length);
  return '<div class="wrap">' + clusters.map((comp) => {
    const hub = comp.reduce((best, n) => ((adj[n] || []).size > (adj[best] || []).size ? n : best), comp[0]);
    return `<div class="cluster"><h3>${esc(hub.replace(/-/g, ' '))}</h3>` +
      edges.filter(([a]) => comp.includes(a)).map(([a, b, k]) =>
        `<div class="edge"><a class="jump" href="#" data-target="i-${esc(a)}">${esc(a)}</a>
         <span class="rel">${esc(k)}</span>
         <a class="jump" href="#" data-target="i-${esc(b)}">${esc(b)}</a></div>`).join('') + '</div>';
  }).join('') + '</div>';
}

function renderSessions(d) {
  const s = d.sessions || [];
  if (!s.length) return '<p class="empty">No sessions recorded.</p>';
  return `<div class="wrap"><table><tr><th>session</th><th>role</th><th>holds</th></tr>` +
    s.map((x) => `<tr><td><code>${esc(x.name)}</code>${x.ref && x.ref !== '—' ? ` <span class="t">[${esc(x.ref)}]</span>` : ''}</td>
      <td>${esc(x.role || '')}</td><td>${esc(x.holds || '')}</td></tr>`).join('') + '</table></div>';
}

function renderWorktrees(text, err) {
  if (err) return `<p class="empty">${esc(err)}</p>`;
  return `<div class="wrap"><pre class="out">${linkify(text || '')}</pre></div>`;
}

module.exports = { shell, esc, linkify, renderBoard, renderQuestions, renderMap, renderSessions, renderWorktrees, allItems };
