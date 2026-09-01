/**
 * StockDesk AI 服务端 — pi RPC 常驻进程的薄壳。
 * 零 npm 依赖（Node 20+ 内置 http）。
 *
 * 职责：Bearer 认证、CORS、会话与 pi 子进程生命周期管理、SSE 透传。
 * 轻/重判断不做：统一交给 pi agent（同一模型自己决定要不要调工具/写代码）。
 */
'use strict';

const http = require('http');
const crypto = require('crypto');
const { spawn } = require('child_process');
const { StringDecoder } = require('string_decoder');
const path = require('path');

const ROOT = __dirname;
const PORT = parseInt(process.env.PORT || '8787', 10);
const TOKEN = process.env.AI_TOKEN || '';
const ALLOW_ORIGIN = process.env.ALLOW_ORIGIN || '*';
const DEFAULT_MODEL = process.env.AI_MODEL || 'kimi/k3';
const DEFAULT_SKILL = process.env.AI_SKILL || '';
const TURN_TIMEOUT_MS = parseInt(process.env.TURN_TIMEOUT_MS || '600000', 10); // 单轮最长10分钟
const SESSION_TTL_MS = 30 * 60 * 1000; // 30分钟空闲回收

function loadDotEnv(file) {
  try {
    for (const line of require('fs').readFileSync(file, 'utf8').split('\n')) {
      const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
      if (m && process.env[m[1]] === undefined) process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
    }
  } catch {}
}
loadDotEnv(path.join(ROOT, '.env'));

const sessions = new Map(); // sid -> {proc, decoder, buffer, model, skill, busy, lastActive, msgId}

function spawnPi(model, skill) {
  const args = [
    '--mode', 'rpc', '--no-session',
    '-e', path.join(ROOT, 'extensions/providers.js'),
    '--provider', model.split('/')[0], '--model', model.split('/')[1],
  ];
  const proc = spawn('pi', args, { cwd: ROOT, env: process.env, stdio: ['pipe', 'pipe', 'inherit'] });
  proc.decoder = new StringDecoder('utf8');
  proc.buffer = '';
  proc.onLine = () => {};
  proc.stdout.on('data', (chunk) => {
    proc.buffer += proc.decoder.write(chunk);
    let i;
    while ((i = proc.buffer.indexOf('\n')) !== -1) {
      const line = proc.buffer.slice(0, i);
      proc.buffer = proc.buffer.slice(i + 1);
      if (line.trim()) proc.onLine(line);
    }
  });
  proc.on('exit', (code) => { for (const [sid, s] of sessions) if (s.proc === proc) { s.dead = code; } });
  return proc;
}

function getSession(sid, model, skill) {
  model = model || DEFAULT_MODEL;
  skill = skill === undefined ? DEFAULT_SKILL : skill;
  let s = sessions.get(sid);
  if (s && !s.dead && s.model === model && s.skill === skill) { s.lastActive = Date.now(); return s; }
  if (s) { try { s.proc.kill(); } catch {} sessions.delete(sid); }
  const proc = spawnPi(model, skill);
  s = { proc, model, skill, busy: false, lastActive: Date.now(), msgId: 0 };
  sessions.set(sid, s);
  return s;
}

function send(proc, cmd) {
  proc.stdin.write(JSON.stringify(cmd) + '\n');
}

// 与 pi 交互：发 prompt，把事件流式回 SSE，agent_end 时结束
function runTurn(s, message, sse) {
  return new Promise((resolve) => {
    const id = 't' + (++s.msgId);
    let textBuf = '';
    let done = false;
    const timer = setTimeout(() => {
      if (done) return; done = true;
      send(s.proc, { type: 'abort' });
      sse.event('error', { message: '本轮执行超时，已中断' });
      sse.event('done', { text: textBuf });
      finish();
    }, TURN_TIMEOUT_MS);

    function finish() {
      clearTimeout(timer);
      s.proc.onLine = () => {};
      s.busy = false;
      s.lastActive = Date.now();
      resolve();
    }

    s.proc.onLine = (line) => {
      let e;
      try { e = JSON.parse(line); } catch { return; }
      const t = e.type;
      if (t === 'response' && e.id === id && e.success === false) {
        sse.event('error', { message: e.error || '消息被拒绝' });
      } else if (t === 'message_update' && e.assistantMessageEvent) {
        const ev = e.assistantMessageEvent;
        if (ev.type === 'text_delta') {
          textBuf += ev.delta;
          sse.event('delta', { text: ev.delta });
        } else if (ev.type === 'thinking_delta') {
          sse.event('thinking', { text: ev.delta });
        }
      } else if (t === 'tool_execution_start') {
        sse.event('tool', { name: e.toolName, args: summarizeArgs(e.args) });
      } else if (t === 'tool_execution_end' && e.isError) {
        sse.event('tool_err', { name: e.toolName });
      } else if (t === 'auto_retry_start') {
        sse.event('retry', { attempt: e.attempt });
      } else if (t === 'agent_end') {
        if (done) return; done = true;
        sse.event('done', { text: textBuf });
        finish();
      }
    };

    s.busy = true;
    send(s.proc, { id, type: 'prompt', message });
  });
}

function summarizeArgs(args) {
  if (!args) return '';
  if (args.command) return String(args.command).slice(0, 120);
  if (args.path) return String(args.path);
  return JSON.stringify(args).slice(0, 120);
}

// 极简 SSE 封装
function sseOf(res) {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache, no-transform',
    'Connection': 'keep-alive',
    'X-Accel-Buffering': 'no',
  });
  return {
    event(name, data) { res.write(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`); },
    comment(s) { res.write(`: ${s}\n\n`); },
  };
}

const ROUTES = {
  'GET /health'(req, res) {
    json(res, 200, { ok: true, defaultModel: DEFAULT_MODEL, sessions: sessions.size });
  },
  'GET /skills'(req, res) {
    // 静态读取 skill 目录即可（name + description 来自 SKILL.md frontmatter）
    const fs = require('fs');
    const dir = path.join(ROOT, '.pi/skills');
    const out = [];
    for (const name of fs.readdirSync(dir)) {
      try {
        const md = fs.readFileSync(path.join(dir, name, 'SKILL.md'), 'utf8');
        const desc = (md.match(/^description:\s*(.+)$/m) || [, ''])[1].trim();
        out.push({ name, description: desc });
      } catch {}
    }
    json(res, 200, { skills: out });
  },
  'GET /models'(req, res) {
    json(res, 200, { models: ['kimi/k3', 'oczen/deepseek-v4-flash-vision-exp'] });
  },
  'DELETE /session'(req, res) {
    const sid = qs(req).get('sid');
    const s = sessions.get(sid);
    if (s) { try { s.proc.kill(); } catch {} sessions.delete(sid); }
    json(res, 200, { cleared: !!s });
  },
};

async function handleChat(req, res) {
  const body = await readBody(req);
  const { sid, message, model, skill } = body;
  if (!sid || !message) return json(res, 400, { error: '缺少 sid 或 message' });
  const s = getSession(String(sid), model, skill);
  if (s.dead) { try { s.proc.kill(); } catch {} sessions.delete(sid); return json(res, 502, { error: '会话已失效，请刷新后重试' }); }
  if (s.busy) return json(res, 409, { error: '上一条回复还在生成中' });
  const sse = sseOf(res);
  sse.comment('ok');
  const hb = setInterval(() => sse.comment('hb'), 15000);
  req.on('close', () => { clearInterval(hb); });
  try {
    await runTurn(s, String(message), sse);
  } finally {
    clearInterval(hb);
    res.end();
  }
}

function qs(req) { return new URL(req.url, 'http://x').searchParams; }
function readBody(req) {
  return new Promise((resolve, reject) => {
    let b = '';
    req.on('data', (c) => { b += c; if (b.length > 1e6) req.destroy(); });
    req.on('end', () => { try { resolve(b ? JSON.parse(b) : {}); } catch (e) { reject(e); } });
    req.on('error', reject);
  });
}
function json(res, code, obj) {
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(obj));
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x');
  const key = req.method + ' ' + url.pathname;
  // CORS 预检
  res.setHeader('Access-Control-Allow-Origin', ALLOW_ORIGIN);
  res.setHeader('Access-Control-Allow-Headers', 'Authorization,Content-Type');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,DELETE,OPTIONS');
  if (req.method === 'OPTIONS') { res.writeHead(204); return res.end(); }
  // 认证（health 除外）
  if (key !== 'GET /health') {
    if (!TOKEN) return json(res, 500, { error: '服务端未配置 AI_TOKEN' });
    const auth = req.headers.authorization || '';
    if (auth !== 'Bearer ' + TOKEN) return json(res, 401, { error: '未授权' });
  }
  try {
    if (key === 'POST /chat') return await handleChat(req, res);
    const h = ROUTES[key];
    if (h) return h(req, res);
    json(res, 404, { error: 'not found' });
  } catch (e) {
    if (!res.headersSent) json(res, 500, { error: String(e.message || e) });
    else res.end();
  }
});

// 空闲会话回收
setInterval(() => {
  const now = Date.now();
  for (const [sid, s] of sessions) {
    if (!s.busy && now - s.lastActive > SESSION_TTL_MS) {
      try { s.proc.kill(); } catch {}
      sessions.delete(sid);
    }
  }
}, 60000);

server.listen(PORT, '127.0.0.1', () => {
  console.log(`stock-ai server on 127.0.0.1:${PORT}, model=${DEFAULT_MODEL}, token=${TOKEN ? 'set' : 'MISSING'}`);
});
