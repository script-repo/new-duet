// A tiny stand-in for the Tandem API (SPEC §10) to develop and screenshot the UI without the Python backend.
// Serves the built app from ../src/tandem/server/static and fakes /api with demo data and a live SSE stream.
// Run: npm run build && node scripts/mock-server.mjs [port]   (set TOKEN=... to exercise the sign-in screen)
// v0.2: /api/connections (Connect goes through a fake consent page and back), push, notify settings, evals.
import { createServer } from 'node:http';
import { randomUUID } from 'node:crypto';
import { readFileSync, existsSync, statSync } from 'node:fs';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';

const PORT = Number(process.argv[2] || 8000);
const TOKEN = process.env.TOKEN || '';
const STATIC = fileURLToPath(new URL('../../src/tandem/server/static/', import.meta.url));
const now = () => new Date().toISOString();
const ago = (m) => new Date(Date.now() - m * 60000).toISOString();
const ahead = (m) => new Date(Date.now() + m * 60000).toISOString();
const todayStr = new Date().toLocaleDateString('en-CA');

let msgId = 0, logId = 0, actId = 0;
const messages = [], logs = {}, activity = [];
const approvals = {}, tasks = {}, artifacts = {}, briefs = {};
const clients = new Set();

function emit(type, data) {
  for (const res of clients) res.write(`event: ${type}\ndata: ${JSON.stringify(data)}\n\n`);
}
function msg(role, text, card = null, at = now()) {
  const m = { id: ++msgId, role, text, card, created_at: at };
  messages.push(m);
  emit('message.created', m);
  return m;
}
function act(kind, title, detail = null, level = 'info', task_id = null, at = now()) {
  const a = { id: ++actId, ts: at, kind, title, detail, task_id, level };
  activity.unshift(a);
  emit('activity.created', a);
}
function log(task_id, kind, text) {
  const l = { id: ++logId, task_id, ts: now(), kind, text };
  (logs[task_id] ??= []).push(l);
  emit('task.log', l);
}

// ---- seed ----
briefs.br_1 = {
  id: 'br_1', date: todayStr, created_at: ago(200),
  greeting: 'Morning. 1 thing in progress and 2 that need you.',
  items: [
    { topic: 'Renewal card declined', summary: 'Vendor says service **suspends at noon** unless the card is updated.', needs_you: true, approval_id: 'ap_pay' },
    { topic: 'Harbor contract', summary: 'Sam replied with redlines; a draft answer is ready for review.', needs_you: true, approval_id: 'ap_mail' },
    { topic: 'Newsletters', summary: '6 newsletters filed away. Nothing important.', needs_you: false },
  ],
};
act('checkin', 'Check-in: 9 new events', '6 ignored (bulk), 2 held for brief, 1 notified', 'info', null, ago(230));
act('brief', 'Morning brief posted', null, 'info', null, ago(200));
msg('agent', briefs.br_1.greeting, { type: 'brief', ref: 'br_1' }, ago(200));
act('triage', 'Notified: Payment failed', 'money + deadline today → high', 'notify', null, ago(90));
msg('agent', '**Payment failed:** your renewal card was declined and the service suspends at noon. Want me to retry with the backup card?', { type: 'notification', ref: 'ev_1', urgency: 'high' }, ago(90));
approvals.ap_pay = {
  id: 'ap_pay', tool: 'payments_pay', title: 'Retry renewal payment', summary: 'Pay Northwind Hosting $49.00 with the backup card',
  reason: 'Moves money', args: { payee: 'Northwind Hosting', amount: 49, currency: 'USD', card: 'backup •••• 4242' },
  editable: ['amount'], tags: ['pay'], state: 'pending', task_id: null, created_at: ago(88), expires_at: ahead(11),
  decided_at: null, note: null, result: null,
};
msg('agent', '', { type: 'approval', ref: 'ap_pay' }, ago(88));
msg('user', 'Build me a one-page cash summary for this week and email it to Sam.', null, ago(30));
tasks.tk_1 = {
  id: 'tk_1', title: 'Cash summary for this week', instructions: 'Build me a one-page cash summary for this week and email it to Sam.',
  harness: 'builtin', origin: 'chat', state: 'running', summary: null, error: null, steps: 4, created_at: ago(29), started_at: ago(29),
  finished_at: null, artifact_ids: ['ar_1'],
};
msg('agent', "On it. I'll pull this week's payments, build a sheet and draft the email.", { type: 'task', ref: 'tk_1' }, ago(29));
for (const [k, t] of [['info', 'Sandbox ready (local)'], ['tool', 'payments_list(since=monday) → 14 rows'], ['tool', 'sandbox_write(cash.csv)'], ['tool', "artifact_create(kind='xlsx', title='Cash summary')"]]) log('tk_1', k, t);
artifacts.ar_1 = { id: 'ar_1', task_id: 'tk_1', kind: 'xlsx', title: 'Cash summary, week 43', filename: 'cash-summary.xlsx', size: 8432, created_at: ago(20) };
artifacts.ar_2 = { id: 'ar_2', task_id: null, kind: 'pptx', title: 'Q4 board update', filename: 'q4-board.pptx', size: 48211, created_at: ago(1500) };
artifacts.ar_3 = { id: 'ar_3', task_id: null, kind: 'docx', title: 'Harbor contract notes', filename: 'harbor-notes.docx', size: 12880, created_at: ago(3000) };
artifacts.ar_4 = { id: 'ar_4', task_id: null, kind: 'md', title: 'Vendor comparison', filename: 'vendors.md', size: 2310, created_at: ago(4000) };
approvals.ap_mail = {
  id: 'ap_mail', tool: 'email_send', title: 'Email Sam the cash summary', summary: 'To sam@harbor.example with cash-summary.xlsx attached',
  reason: 'Sends a message as you', args: { to: 'sam@harbor.example', subject: 'Cash summary, week 43', body: 'Hi Sam,\n\nAttached is this week\'s one-page cash summary. Net cash is up $4,210 on last week, mostly from the Harbor invoice clearing.\n\nBest regards,\nAlex', attachments: ['ar_1'] },
  editable: ['subject', 'body'], tags: ['send'], state: 'pending', task_id: 'tk_1', created_at: ago(18), expires_at: ahead(14), decided_at: null, note: null, result: null,
};
msg('agent', 'The sheet is ready. Here is the email to Sam; nothing goes out until you approve.', { type: 'approval', ref: 'ap_mail' }, ago(18));
act('tool', 'email_send → ask', 'Sends a message as you', 'info', 'tk_1', ago(18));
act('error', 'IMAP poll failed', 'timeout after 30s; will retry next check-in', 'warn', null, ago(12));

// ---- v0.2: connections, push, notify settings, evals ----
const VAPID = 'BNQ7ryKYxFFPM1oybL5X2YRlGanA2LLrRKsYWq2htigl6Jz6bERhAgxpzOmwSrgVKmOYWYcb7szCXkLaawnNigc';
const oauthConn = (name, type, provider, display_name, tools, scopes, over = {}) => ({
  name, type, provider, display_name, oauth: true, configured: true, client_configured: true, connected: false, account: null,
  scopes, expires_at: null, status: 'disconnected', error: null, last_poll: null, tools, push: { mode: 'poll', detail: 'not connected' }, ...over,
});
const connections = [
  { name: 'demo', type: 'fake', provider: null, display_name: 'demo', oauth: false, configured: true, client_configured: true, connected: true, account: null, scopes: [], expires_at: null, status: 'ok', error: null, last_poll: ago(4), tools: ['email_search', 'email_read', 'email_send', 'payments_list', 'payments_pay'], push: { mode: 'poll', detail: '' } },
  { name: 'inbox', type: 'webhook', provider: null, display_name: 'inbox', oauth: false, configured: true, client_configured: true, connected: true, account: null, scopes: [], expires_at: null, status: 'ok', error: null, last_poll: null, tools: [], push: { mode: 'push', detail: '' } },
  { name: 'work', type: 'imap', provider: null, display_name: 'work', oauth: false, configured: true, client_configured: true, connected: false, account: null, scopes: [], expires_at: null, status: 'error', error: 'timeout after 30s', last_poll: ago(12), tools: ['work_search', 'work_read', 'work_send'], push: { mode: 'poll', detail: '' } },
  oauthConn('gmail', 'gmail', 'google', 'Gmail', ['gmail_search', 'gmail_read', 'gmail_send', 'gmail_reply'], ['openid', 'email', 'https://www.googleapis.com/auth/gmail.readonly', 'https://www.googleapis.com/auth/gmail.send'], {
    connected: true, status: 'ok', account: 'alex@harbor.example', last_poll: ago(3), expires_at: ahead(50), push: { mode: 'push', detail: 'Pub/Sub watch until Oct 28' },
  }),
  oauthConn('calendar', 'google_calendar', 'google', 'Google Calendar', ['calendar_list', 'calendar_create'], ['openid', 'email', 'https://www.googleapis.com/auth/calendar.events'], {
    connected: false, status: 'error', account: 'alex@harbor.example', error: 'Token refresh failed: invalid_grant (access was revoked)', push: { mode: 'poll', detail: 'needs reconnect' },
  }),
  oauthConn('slack', 'slack', 'slack', 'Slack', ['slack_search', 'slack_history', 'slack_post'], ['im:history', 'chat:write'], { configured: false }),
];
const available = [
  { type: 'gmail', provider: 'google', display_name: 'Gmail', default_name: 'gmail', env: ['GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET'], client_configured: true, in_use: true, docs: 'docs/CONNECTORS.md' },
  { type: 'google_calendar', provider: 'google', display_name: 'Google Calendar', default_name: 'calendar', env: ['GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET'], client_configured: true, in_use: true, docs: 'docs/CONNECTORS.md' },
  { type: 'm365', provider: 'microsoft', display_name: 'Microsoft 365', default_name: 'outlook', env: ['MS_CLIENT_ID', 'MS_CLIENT_SECRET'], client_configured: false, in_use: false, docs: 'docs/CONNECTORS.md' },
  { type: 'slack', provider: 'slack', display_name: 'Slack', default_name: 'slack', env: ['SLACK_CLIENT_ID', 'SLACK_CLIENT_SECRET'], client_configured: true, in_use: true, docs: 'docs/CONNECTORS.md' },
];
let notifySettings = { min_urgency: 'high', quiet_hours: '21:00-07:00', max_per_hour: 4, push_enabled: true };
let pushSubs = [
  { id: 'push_ab12', host: 'web.push.apple.com', label: 'Safari on iPhone', created_at: ago(4000), last_ok_at: ago(95), last_error: null, failures: 0 },
  { id: 'push_cd34', host: 'fcm.googleapis.com', label: 'Chrome on Windows', created_at: ago(9000), last_ok_at: ago(3000), last_error: '403 Forbidden (VAPID key changed?)', failures: 2 },
];
const evals = JSON.parse(readFileSync(new URL('./mock-evals.json', import.meta.url), 'utf8'));
let evalRunning = false;
function runEval(model) {
  evalRunning = true;
  setTimeout(() => {
    evalRunning = false;
    const t = structuredClone(evals.triage);
    const jitter = model === 'noisy' ? -0.08 : 0.03;
    t.model = model;
    t.ran_at = now();
    t.metrics.accuracy = Math.max(0, Math.min(1, +(t.metrics.accuracy + jitter).toFixed(4)));
    evals.triage = t;
    evals.history.unshift({ kind: 'triage', model, ran_at: t.ran_at, headline: t.metrics.accuracy, missed_urgent: t.metrics.missed_urgent });
    act('eval', `Triage eval: ${Math.round(t.metrics.accuracy * 100)}% accurate (${model})`);
    emit('eval.updated', { kind: 'triage' });
  }, 2500);
}

const config = {
  mode: 'mock', timezone: 'America/Toronto',
  models: { triage: { name: 'cheap', provider: 'mock', model: 'mock-cheap', has_api_key: false }, brief: { name: 'strong', provider: 'anthropic', model: 'claude-opus-5-5', has_api_key: true }, chat: { name: 'strong', provider: 'anthropic', model: 'claude-opus-5-5', has_api_key: true }, worker: { name: 'local', provider: 'openai_compatible', model: 'llama-3.1-70b', has_api_key: false } },
  harnesses: [
    { name: 'builtin', display_name: 'Built-in', available: true, reason: null, default: true },
    { name: 'deepagents', display_name: 'LangChain Deep Agents', available: true, reason: null, default: false },
    { name: 'claude-sdk', display_name: 'Claude Agent SDK', available: false, reason: 'Install the [claude-sdk] extra', default: false },
    { name: 'cli', display_name: 'CLI coding agent', available: false, reason: 'Neither claude nor codex is on PATH', default: false },
  ],
  connectors: [
    { name: 'email', type: 'fake', status: 'ok', tools: ['email_search', 'email_read', 'email_send'] },
    { name: 'calendar', type: 'fake', status: 'ok', tools: ['calendar_list', 'calendar_create'] },
    { name: 'payments', type: 'fake', status: 'ok', tools: ['payments_list', 'payments_pay'] },
    { name: 'work', type: 'imap', status: { state: 'error', detail: 'timeout' }, tools: ['work_search', 'work_read', 'work_send'] },
  ],
  policy: { default: 'allow', timeout_s: 900, rules: [
    { match: { tool: 'payments_pay', args: { amount: { gt: 5000 } } }, action: 'deny', reason: 'Payments over $5,000 are never made by the agent' },
    { match: { tags: ['send'] }, action: 'ask', reason: 'Sends a message as you' },
    { match: { tags: ['pay'] }, action: 'ask', reason: 'Moves money' },
    { match: { tags: ['delete'] }, action: 'ask' },
    { match: { tool: 'sandbox_exec', sandbox: 'local' }, action: 'ask', reason: 'Runs a command without container isolation' },
  ] },
  schedule: { checkin_every: '15m', brief_at: '07:30' }, notify: { min_urgency: 'high', push: { enabled: true, subscriptions: 0, vapid_public_key: VAPID } }, voice: { server: true }, auth: { required: !!TOKEN },
};
let memory = [
  { id: 'mm_1', kind: 'core', key: null, content: 'Runs a small design studio; works mostly mornings.', source: 'user', created_at: ago(9000), updated_at: ago(9000) },
  { id: 'mm_2', kind: 'preference', key: 'signoff', content: 'Prefers shorter sign-offs in email to Harbor.', source: 'edit', created_at: ago(3000), updated_at: ago(3000) },
  { id: 'mm_3', kind: 'preference', key: 'vip', content: 'vip: sam@harbor.example', source: 'user', created_at: ago(5000), updated_at: ago(5000) },
];
const outbox = { messages: [{ to: 'ops@northwind.example', subject: 'Re: invoice 1182', body: 'Thanks, paid today.\n\nD', sent_at: ago(1440) }], payments: [] };
const audit = [
  { id: 3, ts: ago(18), actor: 'tk_1', tool: 'email_send', args: {}, decision: 'approved', approval_id: 'ap_x', task_id: 'tk_1', ok: true, detail: null },
  { id: 2, ts: ago(25), actor: 'tk_1', tool: 'payments_list', args: {}, decision: 'allow', approval_id: null, task_id: 'tk_1', ok: true, detail: '14 rows' },
  { id: 1, ts: ago(300), actor: 'brief', tool: 'payments_pay', args: {}, decision: 'expired', approval_id: 'ap_y', task_id: null, ok: false, detail: 'No decision within 15 min' },
];
const doneTask = { id: 'tk_0', title: 'Reconcile October invoices', instructions: '', harness: 'deepagents', origin: 'brief', state: 'done', summary: 'Matched 22 of 23 invoices; one flagged.', error: null, steps: 9, created_at: ago(180), started_at: ago(180), finished_at: ago(160), artifact_ids: [] };
tasks.tk_0 = doneTask;

function today() {
  return {
    date: todayStr, brief: briefs.br_1,
    needs_you: Object.values(approvals).filter((a) => a.state === 'pending'),
    in_progress: Object.values(tasks).filter((t) => ['queued', 'running', 'waiting'].includes(t.state)),
    done: Object.values(tasks).filter((t) => t.state === 'done'),
    upcoming: [
      { id: 'c1', title: 'Call with Sam (Harbor)', start: ahead(75), end: ahead(105), location: 'Zoom' },
      { id: 'c2', title: 'Dentist', start: ahead(300), end: ahead(360), location: 'Front St.' },
      { id: 'c3', title: 'Recycling day', start: todayStr + 'T00:00:00', all_day: true },
    ],
    notifications: [{ id: 'ev_1', source: 'email', kind: 'email', title: 'Payment failed: renewal card declined', sender: 'billing@northwind.example', occurred_at: ago(92), status: 'notified', urgency: 'high', triage: { reason: 'money + deadline today' } }],
    stats: { events_today: 23, ignored: 17, notified: 1, tasks_done: 1 },
  };
}

// ---- live demo ticker: the running task keeps logging ----
const steps = ['thought: summarizing inflows vs outflows', "tool: artifact_create(kind='md', title='Cash notes')", 'info: waiting on approval ap_mail', 'thought: checking for duplicate payments', 'tool: payments_list(status=failed) → 1 row'];
let si = 0;
setInterval(() => {
  const t = tasks.tk_1;
  if (t.state !== 'running') return;
  const [k, ...rest] = steps[si++ % steps.length].split(': ');
  log('tk_1', k, rest.join(': '));
  t.steps++;
  emit('task.updated', t);
}, 6000);

function decide(a, body) {
  a.state = body.decision === 'reject' ? 'rejected' : 'approved';
  if (body.decision === 'edit') a.args = { ...a.args, ...Object.fromEntries(a.editable.filter((k) => k in body.args).map((k) => [k, body.args[k]])) };
  a.decided_at = now();
  a.note = body.note ?? null;
  emit('approval.updated', a);
  act('approval', `${a.tool} ${a.state}`, body.note ?? null, 'info', a.task_id);
  if (a.id === 'ap_mail' && a.state === 'approved') {
    setTimeout(() => {
      const t = tasks.tk_1;
      Object.assign(t, { state: 'done', finished_at: now(), summary: 'Built **cash-summary.xlsx** and emailed it to Sam.' });
      emit('task.updated', t);
      msg('agent', 'Done. The summary went to Sam.', { type: 'artifact', ref: 'ar_1' });
    }, 1500);
  }
  return a;
}

function chatReply(text) {
  setTimeout(() => emit('typing', { on: true }), 300);
  setTimeout(() => {
    emit('typing', { on: false });
    if (/remember/i.test(text)) {
      const m = { id: 'mm_' + Date.now(), kind: 'preference', key: null, content: text.replace(/^.*?remember( that)?/i, '').trim(), source: 'chat', created_at: now(), updated_at: now() };
      memory.unshift(m);
      msg('agent', `Noted: _${m.content}_`);
    } else {
      msg('agent', `Here's what I know: **2 things** need you today, and the cash summary is still running.\n\n- Payment retry expires soon\n- Email to Sam is drafted`);
      emit('notify', { title: 'Tandem replied', body: 'Two things need you today.', urgency: 'normal', message_id: msgId });
    }
  }, 2200);
}

// ---- http ----
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.webmanifest': 'application/manifest+json' };
function send(res, code, body, type = 'application/json') {
  res.writeHead(code, { 'Content-Type': type });
  res.end(type === 'application/json' ? JSON.stringify(body) : body);
}
function readBody(req) {
  return new Promise((ok) => {
    let b = '';
    req.on('data', (c) => (b += c));
    req.on('end', () => {
      try { ok(b ? JSON.parse(b) : {}); } catch { ok({}); }
    });
  });
}
function authed(req) {
  if (!TOKEN) return true;
  return (req.headers.cookie || '').split(/;\s*/).includes(`tandem_token=${TOKEN}`) || req.headers.authorization === `Bearer ${TOKEN}`;
}

createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x');
  const p = url.pathname;
  if (!p.startsWith('/api/')) {
    let file = normalize(join(STATIC, p === '/' ? 'index.html' : p));
    if (!file.startsWith(STATIC) || !existsSync(file) || statSync(file).isDirectory()) file = join(STATIC, 'index.html');
    return send(res, 200, readFileSync(file), TYPES[extname(file)] || 'application/octet-stream');
  }
  if (p === '/api/health') return send(res, 200, { ok: true, version: '0.1.0', mode: 'mock' });
  if (p === '/api/auth/login' && req.method === 'POST') {
    const { token } = await readBody(req);
    if (TOKEN && token !== TOKEN) return send(res, 401, { detail: 'Invalid token' });
    res.setHeader('Set-Cookie', `tandem_token=${token}; Path=/; HttpOnly; SameSite=Lax`);
    return send(res, 200, { ok: true });
  }
  if (!authed(req)) return send(res, 401, { detail: 'Not authenticated' });

  if (p === '/api/stream') {
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' });
    res.write(`event: hello\ndata: ${JSON.stringify({ server_time: now(), version: '0.1.0' })}\n\n`);
    clients.add(res);
    const ping = setInterval(() => res.write(': ping\n\n'), 15000);
    req.on('close', () => { clients.delete(res); clearInterval(ping); });
    return;
  }
  const m = (re) => p.match(re);
  let r;
  if (p === '/api/config') return send(res, 200, config);
  if (p === '/api/thread' && req.method === 'GET') {
    const limit = Number(url.searchParams.get('limit') || 50);
    const before = Number(url.searchParams.get('before') || Infinity);
    const page = messages.filter((x) => x.id < before).slice(-limit);
    return send(res, 200, { messages: page, approvals, tasks, artifacts, briefs });
  }
  if (p === '/api/thread' && req.method === 'POST') {
    const { text } = await readBody(req);
    const message = msg('user', text);
    chatReply(text);
    return send(res, 200, { message });
  }
  if ((r = m(/^\/api\/approvals\/([^/]+)\/decision$/))) return send(res, 200, decide(approvals[r[1]], await readBody(req)));
  if (p === '/api/approvals') return send(res, 200, Object.values(approvals));
  if (p === '/api/tasks') return send(res, 200, Object.values(tasks));
  if ((r = m(/^\/api\/tasks\/([^/]+)\/steer$/))) {
    const { text } = await readBody(req);
    log(r[1], 'steer', text);
    return send(res, 200, tasks[r[1]]);
  }
  if ((r = m(/^\/api\/tasks\/([^/]+)\/stop$/))) {
    Object.assign(tasks[r[1]], { state: 'stopped', finished_at: now() });
    emit('task.updated', tasks[r[1]]);
    return send(res, 200, tasks[r[1]]);
  }
  if ((r = m(/^\/api\/tasks\/([^/]+)$/))) return send(res, 200, { ...tasks[r[1]], logs: logs[r[1]] ?? [] });
  if (p === '/api/activity') {
    const before = Number(url.searchParams.get('before') || Infinity);
    return send(res, 200, activity.filter((a) => a.id < before).slice(0, Number(url.searchParams.get('limit') || 100)));
  }
  if (p === '/api/today') return send(res, 200, today());
  if (p === '/api/brief/run') { emit('brief.created', briefs.br_1); return send(res, 200, briefs.br_1); }
  if (p === '/api/checkin/run') { act('checkin', 'Check-in: 0 new events'); return send(res, 200, { new_events: 0, notified: 0, ignored: 0 }); }
  if (p === '/api/artifacts') return send(res, 200, Object.values(artifacts));
  if ((r = m(/^\/api\/artifacts\/([^/]+)\/preview$/))) {
    const a = artifacts[r[1]];
    return send(res, 200, `<!doctype html><meta charset=utf-8><style>body{font:15px system-ui;margin:32px}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 10px}</style><h1>${a.title}</h1><table><tr><th>Day</th><th>In</th><th>Out</th></tr><tr><td>Mon</td><td>4,200</td><td>1,100</td></tr><tr><td>Tue</td><td>900</td><td>2,050</td></tr></table>`, 'text/html');
  }
  if ((r = m(/^\/api\/artifacts\/([^/]+)\/download$/))) return send(res, 200, 'demo', 'application/octet-stream');
  if (p === '/api/memory' && req.method === 'GET') return send(res, 200, memory);
  if (p === '/api/memory' && req.method === 'POST') {
    const b = await readBody(req);
    const e = { id: 'mm_' + Date.now(), kind: b.kind, key: b.key ?? null, content: b.content, source: 'user', created_at: now(), updated_at: now() };
    memory = [e, ...memory.filter((x) => !(e.key && x.key === e.key))];
    return send(res, 200, e);
  }
  if ((r = m(/^\/api\/memory\/([^/]+)$/)) && req.method === 'DELETE') { memory = memory.filter((x) => x.id !== r[1]); return send(res, 200, { ok: true }); }
  if (p === '/api/skills') return send(res, 200, [{ name: 'cash-summary', description: 'One-page weekly cash summary as a sheet' }, { name: 'reply-drafts', description: 'Draft replies in the user\'s voice' }]);
  if (p === '/api/audit') return send(res, 200, audit);
  if (p === '/api/outbox') return send(res, 200, outbox);
  if (p === '/api/connections' && req.method === 'GET') {
    return send(res, 200, { connections, available, redirect_uri: `http://127.0.0.1:${PORT}/api/oauth/callback`, public_url: `http://127.0.0.1:${PORT}`, push_capable: false });
  }
  if ((r = m(/^\/api\/connections\/([^/]+)\/authorize$/)) && req.method === 'POST') {
    const name = decodeURIComponent(r[1]);
    const a = available.find((x) => x.default_name === name);
    if (a && !a.client_configured) return send(res, 400, { detail: `Set ${a.env.join(' and ')} to connect ${a.display_name}` });
    if (!a && !connections.some((c) => c.name === name && c.oauth)) return send(res, 400, { detail: 'no such connector' });
    // stands in for the provider's consent screen: straight back with ?connected=
    return send(res, 200, { url: `/api/oauth/mock?name=${encodeURIComponent(name)}` });
  }
  if (p === '/api/oauth/mock') {
    const name = url.searchParams.get('name');
    let c = connections.find((x) => x.name === name);
    if (!c) {
      const a = available.find((x) => x.default_name === name);
      c = oauthConn(name, a.type, a.provider, a.display_name, [`${name}_search`], []);
      connections.push(c);
    }
    Object.assign(c, { connected: true, status: 'ok', error: null, last_poll: null, push: { mode: 'poll', detail: 'connecting…' } });
    setTimeout(() => {
      Object.assign(c, { account: 'alex@harbor.example', last_poll: now(), push: { mode: 'poll', detail: 'no public HTTPS URL; polled each check-in' } });
      act('connector', `${c.display_name} connected`, 'alex@harbor.example');
    }, 1200);
    res.writeHead(302, { Location: `/#/connections?connected=${encodeURIComponent(name)}` });
    return res.end();
  }
  if ((r = m(/^\/api\/connections\/([^/]+)$/)) && req.method === 'DELETE') {
    const c = connections.find((x) => x.name === decodeURIComponent(r[1]));
    if (!c) return send(res, 404, { detail: 'no such connector' });
    Object.assign(c, { connected: false, status: 'disconnected', account: null, error: null, push: { mode: 'poll', detail: 'not connected' } });
    return send(res, 200, { ok: true, revoked: c.provider !== 'microsoft' });
  }
  if (p === '/api/push/vapid') return send(res, 200, { public_key: VAPID, enabled: true });
  if (p === '/api/push/subscriptions' && req.method === 'GET') return send(res, 200, pushSubs);
  if (p === '/api/push/subscriptions' && req.method === 'POST') {
    const b = await readBody(req);
    if (!String(b.endpoint || '').startsWith('https://')) return send(res, 422, { detail: 'endpoint must be https' });
    const s = { id: 'push_' + randomUUID().slice(0, 8), host: new URL(b.endpoint).host, label: 'This browser', created_at: now(), last_ok_at: null, last_error: null, failures: 0 };
    pushSubs.push(s);
    return send(res, 200, s);
  }
  if (p === '/api/push/subscriptions' && req.method === 'DELETE') {
    const b = await readBody(req);
    pushSubs = pushSubs.filter((x) => x.id !== b.id);
    return send(res, 200, { ok: true });
  }
  if (p === '/api/push/test' && req.method === 'POST') {
    if (!notifySettings.push_enabled || !pushSubs.length) return send(res, 409, { detail: notifySettings.push_enabled ? 'no device has turned on push' : 'push is turned off' });
    const ok = pushSubs.filter((x) => !x.last_error);
    for (const x of ok) x.last_ok_at = now();
    return send(res, 200, { delivered: ok.length, subscriptions: pushSubs.length });
  }
  if (p === '/api/settings/notify' && req.method === 'GET') return send(res, 200, notifySettings);
  if (p === '/api/settings/notify' && req.method === 'PUT') {
    const b = await readBody(req);
    if ('min_urgency' in b && !['low', 'normal', 'high', 'critical'].includes(b.min_urgency)) return send(res, 422, { detail: 'min_urgency must be low, normal, high or critical' });
    if ('quiet_hours' in b && b.quiet_hours !== null && !/^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$/.test(b.quiet_hours)) return send(res, 422, { detail: 'quiet_hours must be HH:MM-HH:MM' });
    if ('max_per_hour' in b && !(Number.isInteger(b.max_per_hour) && b.max_per_hour >= 1)) return send(res, 422, { detail: 'max_per_hour must be a positive integer' });
    notifySettings = { ...notifySettings, ...b };
    return send(res, 200, notifySettings);
  }
  if (p === '/api/evals') return send(res, 200, { ...evals, running: evalRunning ? ['triage'] : [] });
  if (p === '/api/evals/triage/run' && req.method === 'POST') {
    const { model = 'triage' } = await readBody(req);
    const allowed = ['triage', 'brief', 'chat', 'worker', 'cheap', 'strong', 'local', 'mock', 'noisy'];
    if (!allowed.includes(model)) return send(res, 400, { detail: `model must be one of ${allowed.join(', ')}` });
    if (evalRunning) return send(res, 409, { detail: 'a triage eval is already running' });
    runEval(model);
    return send(res, 202, { started: true, model });
  }
  if (p === '/api/voice/transcribe') return send(res, 200, { text: 'Remind me to call Sam at four.' });
  send(res, 404, { detail: 'Not found' });
}).listen(PORT, '127.0.0.1', () => console.log(`mock Tandem API on http://127.0.0.1:${PORT}`));
