'use strict';

const vscode = require('vscode');
const fs = require('fs');
const path = require('path');
const cp = require('child_process');
const R = require('./render');

let ctx, board = null, panel = null, pagePanel = null;
const views = {};              // view id -> WebviewView
let watchers = [], timer = null;

const nonce = () => Array.from({ length: 32 }, () =>
  'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'[Math.floor(Math.random() * 62)]).join('');

const cfg = () => vscode.workspace.getConfiguration('workboard');

// --------------------------------------------------------------------- board data

/** Every folder in the workspace holding a .workboard/items.json. */
async function discoverBoards() {
  const hits = await vscode.workspace.findFiles('**/.workboard/items.json', '**/node_modules/**', 50);
  return hits.map((u) => {
    const dir = path.dirname(path.dirname(u.fsPath));
    const ws = vscode.workspace.getWorkspaceFolder(u);
    const rel = ws ? path.relative(ws.uri.fsPath, dir) : dir;
    return {
      dir,
      name: rel === '' ? path.basename(dir) : rel,
      workspace: ws ? ws.uri.fsPath : path.dirname(dir),
    };
  }).sort((a, b) => a.name.localeCompare(b.name));
}

function loadBoard(b) {
  const data = JSON.parse(fs.readFileSync(path.join(b.dir, '.workboard', 'items.json'), 'utf8'));
  const notes = {};
  const nf = path.join(b.dir, '.workboard', 'notes.jsonl');
  if (fs.existsSync(nf)) {
    fs.readFileSync(nf, 'utf8').split('\n').forEach((line, i) => {
      line = line.trim();
      if (!line) return;
      try {
        const n = JSON.parse(line);
        (notes[n.item || '?'] = notes[n.item || '?'] || []).push(n);
      } catch (e) {
        // Why surface rather than skip: a silently dropped note is a worker who believes
        // it reported and is invisible. Name the bad line instead of hiding it.
        (notes.__malformed = notes.__malformed || []).push(
          { who: 'notes.jsonl', ts: '', note: `line ${i + 1} is not valid JSON: ${e.message}` });
      }
    });
  }
  return { data, notes };
}

/** Worktree status straight from the gate, so scope rules live in exactly one place. */
function gateStatus(b) {
  const server = path.join(b.workspace, '.worktree-gate', 'server.py');
  if (!fs.existsSync(server)) return { err: 'No .worktree-gate in this workspace.' };
  const scope = path.relative(b.workspace, b.dir) || '.';
  try {
    return {
      text: cp.execFileSync(cfg().get('pythonPath') || 'python3',
        [server, 'call', 'worktree_status', JSON.stringify({ scope })],
        { encoding: 'utf8', timeout: 20000, cwd: b.workspace }),
    };
  } catch (e) {
    return { err: (e.stderr || e.message || String(e)).trim() };
  }
}

// --------------------------------------------------------------------- rendering

function html(webview, bodyFn, title) {
  if (!board) {
    return R.shell({ nonce: nonce(), csp: webview.cspSource, title,
      body: '<p class="empty">No .workboard found in this workspace.</p>' });
  }
  let body;
  try {
    body = bodyFn();
  } catch (e) {
    body = `<p class="empty">Could not read the board: ${R.esc(e.message)}</p>`;
  }
  return R.shell({ nonce: nonce(), csp: webview.cspSource, body, title });
}

const VIEW_BODY = {
  'workboard.questions': () => R.renderQuestions(loadBoard(board).data),
  'workboard.map': () => R.renderMap(loadBoard(board).data),
  'workboard.sessions': () => R.renderSessions(loadBoard(board).data),
  'workboard.worktrees': () => {
    const s = gateStatus(board);
    return R.renderWorktrees(s.text, s.err);
  },
};

function refresh() {
  if (panel) {
    panel.webview.html = html(panel.webview, () => {
      const { data, notes } = loadBoard(board);
      return R.renderBoard(data, notes, board);
    }, `Workboard — ${board ? board.name : ''}`);
  }
  for (const [id, fn] of Object.entries(VIEW_BODY)) {
    if (views[id]) views[id].webview.html = html(views[id].webview, fn, id);
  }
}

const scheduleRefresh = () => { clearTimeout(timer); timer = setTimeout(refresh, 150); };

// --------------------------------------------------------------------- file links

function commonSuffix(a, b) {
  let n = 0;
  while (n < a.length && n < b.length && a[a.length - 1 - n] === b[b.length - 1 - n]) n++;
  return n;
}

// Repo roots this workspace actually uses; evidence leaves them implicit.
const MIDS = ['', 'public/src', 'public', 'src', 'public/tests', 'tests'];

function repoDirs(ws) {
  try {
    return fs.readdirSync(ws).filter((d) => fs.existsSync(path.join(ws, d, '.git')));
  } catch (e) {
    return [];
  }
}

/**
 * Which repos a `repo ::` hint could mean.
 * Why fuzzy: evidence says `FDW ::` but the directory is `fractaldataworks`, and a hint
 * that resolves to nothing should widen the search rather than fail the link.
 */
function candidateRepos(ws, hint) {
  const all = repoDirs(ws);
  if (!hint) return all;
  const alias = cfg().get('repoAliases') || {};
  if (alias[hint] && all.includes(alias[hint])) return [alias[hint], ...all.filter((r) => r !== alias[hint])];
  const exact = all.find((r) => r.toLowerCase() === hint.toLowerCase());
  if (exact) return [exact, ...all.filter((r) => r !== exact)];
  const near = all.filter((r) => r.toLowerCase().includes(hint.toLowerCase()));
  return [...near, ...all.filter((r) => !near.includes(r))];
}

async function openLoc(loc) {
  if (!board || !loc || !loc.path) return;
  const ws = board.workspace;
  const cands = [];
  for (const repo of candidateRepos(ws, loc.repo)) {
    for (const mid of MIDS) cands.push(path.join(ws, repo, mid, loc.path));
  }
  cands.push(path.join(board.dir, loc.path), path.join(ws, loc.path));

  let hit = cands.find((c) => fs.existsSync(c) && fs.statSync(c).isFile());
  if (!hit) {
    // Fall back to a workspace search on the basename, preferring the longest path-suffix match.
    const found = await vscode.workspace.findFiles(
      `**/${path.basename(loc.path)}`, '**/node_modules/**', 200);
    const scored = found
      .map((u) => ({ u, score: commonSuffix(u.fsPath.split(path.sep), loc.path.split('/')) }))
      .filter((x) => x.score > 0)
      .sort((a, b) => b.score - a.score);
    if (scored.length) hit = scored[0].u.fsPath;
  }
  if (!hit) {
    vscode.window.showWarningMessage(
      `Workboard: could not find ${loc.repo ? loc.repo + ' :: ' : ''}${loc.path}. ` +
      `Tried ${cands.length} candidate paths and a workspace search.`);
    return;
  }

  const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(hit));
  const ed = await vscode.window.showTextDocument(doc, { preview: true, viewColumn: vscode.ViewColumn.One });
  if (loc.line) {
    const line = Math.max(0, Math.min(loc.line - 1, doc.lineCount - 1));
    const pos = new vscode.Position(line, 0);
    ed.selection = new vscode.Selection(pos, pos);
    ed.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
  }
}

async function onMessage(msg) {
  if (!msg) return;
  if (msg.type === 'open') await openLoc(msg.loc);
  if (msg.type === 'reveal') {
    await openPanel();
    if (panel) panel.webview.postMessage({ type: 'scrollTo', id: msg.id });
  }
}

// --------------------------------------------------------------------- wiring

async function openPanel() {
  if (panel) { panel.reveal(vscode.ViewColumn.Active, false); return; }
  panel = vscode.window.createWebviewPanel(
    'workboard.board', `Workboard — ${board ? board.name : ''}`,
    { viewColumn: vscode.ViewColumn.Active, preserveFocus: false },
    { enableScripts: true, retainContextWhenHidden: true });
  panel.onDidDispose(() => { panel = null; }, null, ctx.subscriptions);
  panel.webview.onDidReceiveMessage(onMessage, null, ctx.subscriptions);
  refresh();
}

/**
 * The generated WORKBOARD.html, whole, in a webview that reloads when it changes.
 *
 * The page is self-contained (inline CSS and JS, no external assets), so it only needs a
 * CSP that permits inline, plus a shim that restores scroll — setting `webview.html`
 * replaces the document, and a monitor that jumps to the top on every refresh is useless.
 */
function pageHtml(file) {
  let doc = fs.readFileSync(file, 'utf8');
  const inject = `<meta http-equiv="Content-Security-Policy"
    content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; script-src 'unsafe-inline';">
<script>
  (function () {
    const s = acquireVsCodeApi();
    const prev = s.getState();
    if (prev && typeof prev.y === 'number') {
      window.addEventListener('load', () => window.scrollTo(0, prev.y));
    }
    let t;
    window.addEventListener('scroll', () => {
      clearTimeout(t);
      t = setTimeout(() => s.setState({ y: window.scrollY }), 120);
    }, { passive: true });
  }());
</script>`;
  if (/<head[^>]*>/i.test(doc)) doc = doc.replace(/<head[^>]*>/i, (m) => m + inject);
  else doc = inject + doc;
  return doc;
}

async function openPage() {
  if (!board) return;
  const file = path.join(board.dir, 'WORKBOARD.html');
  if (!fs.existsSync(file)) {
    const build = path.join(board.dir, '.workboard', 'build.py');
    if (!fs.existsSync(build)) {
      vscode.window.showWarningMessage('Workboard: no WORKBOARD.html and no build.py for this board.');
      return;
    }
    await vscode.commands.executeCommand('workboard.rebuild');
    if (!fs.existsSync(file)) return;
  }
  if (!pagePanel) {
    pagePanel = vscode.window.createWebviewPanel('workboard.page',
      `Workboard page — ${board.name}`,
      { viewColumn: vscode.ViewColumn.Active, preserveFocus: false },
      { enableScripts: true, retainContextWhenHidden: true });
    pagePanel.onDidDispose(() => { pagePanel = null; }, null, ctx.subscriptions);
  } else {
    pagePanel.reveal(vscode.ViewColumn.Active, false);
  }
  pagePanel.webview.html = pageHtml(file);
}

function makeViewProvider(id) {
  return {
    resolveWebviewView(view) {
      views[id] = view;
      view.webview.options = { enableScripts: true };
      view.webview.onDidReceiveMessage(onMessage, null, ctx.subscriptions);
      view.onDidDispose(() => { delete views[id]; });
      refresh();
    },
  };
}

function onData(uri) {
  if (/WORKBOARD\.html$/.test(uri.fsPath)) {
    if (pagePanel && board && uri.fsPath === path.join(board.dir, 'WORKBOARD.html')) {
      try { pagePanel.webview.html = pageHtml(uri.fsPath); } catch (e) { /* mid-write */ }
    }
    return;
  }
  // Why ignore the autoRebuild setting while the page view is open: that view shows the
  // GENERATED page, so leaving it stale would make the command look broken.
  if ((cfg().get('autoRebuild') || pagePanel) && board
      && /(items\.json|notes\.jsonl)$/.test(uri.fsPath)
      && uri.fsPath.startsWith(board.dir)) {
    const build = path.join(board.dir, '.workboard', 'build.py');
    if (fs.existsSync(build)) {
      try {
        cp.execFileSync(cfg().get('pythonPath') || 'python3', [build],
          { cwd: board.workspace, timeout: 60000 });
      } catch (e) {
        // The generated page is optional here -- this extension reads the data directly,
        // so a failed rebuild must not stop the views from refreshing.
      }
    }
  }
  scheduleRefresh();
}

function watch() {
  watchers.forEach((w) => w.dispose());
  watchers = [];
  for (const glob of ['**/.workboard/items.json', '**/.workboard/notes.jsonl',
                      '**/.worktree-gate/ledger.jsonl', '**/WORKBOARD.html']) {
    const w = vscode.workspace.createFileSystemWatcher(glob);
    w.onDidChange(onData);
    w.onDidCreate(onData);
    w.onDidDelete(onData);
    watchers.push(w);
    ctx.subscriptions.push(w);
  }
}

async function pickBoard(boards, ask) {
  const configured = cfg().get('boardPath');
  if (configured) {
    const m = boards.find((b) => b.dir === configured || b.name === configured);
    if (m) return m;
  }
  if (!boards.length) return null;
  if (!ask || boards.length === 1) return boards[0];
  const pick = await vscode.window.showQuickPick(
    boards.map((b) => ({ label: b.name, description: b.dir, board: b })),
    { placeHolder: 'Which board?' });
  return pick ? pick.board : board;
}

async function activate(context) {
  ctx = context;
  board = await pickBoard(await discoverBoards(), false);

  for (const id of Object.keys(VIEW_BODY)) {
    context.subscriptions.push(vscode.window.registerWebviewViewProvider(id, makeViewProvider(id)));
  }

  context.subscriptions.push(
    vscode.commands.registerCommand('workboard.open', openPanel),
    vscode.commands.registerCommand('workboard.refresh', refresh),
    vscode.commands.registerCommand('workboard.rebuild', () => {
      if (!board) return;
      const build = path.join(board.dir, '.workboard', 'build.py');
      if (!fs.existsSync(build)) {
        vscode.window.showWarningMessage('Workboard: this board has no build.py.');
        return;
      }
      try {
        const out = cp.execFileSync(cfg().get('pythonPath') || 'python3', [build],
          { cwd: board.workspace, encoding: 'utf8', timeout: 60000 });
        vscode.window.showInformationMessage(`Workboard: ${out.trim()}`);
      } catch (e) {
        vscode.window.showErrorMessage(`Workboard build failed: ${(e.stderr || e.message).trim()}`);
      }
      refresh();
    }),
    vscode.commands.registerCommand('workboard.selectBoard', async () => {
      board = await pickBoard(await discoverBoards(), true);
      if (panel) panel.title = `Workboard — ${board ? board.name : ''}`;
      if (pagePanel) { pagePanel.dispose(); pagePanel = null; openPage(); }
      refresh();
    }),
    vscode.commands.registerCommand('workboard.openPage', openPage),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration('workboard')) scheduleRefresh();
    }));

  watch();
  refresh();
}

function deactivate() { watchers.forEach((w) => w.dispose()); }

module.exports = { activate, deactivate };
