/**
 * StockDesk AI Worker — 出站轮询数据服务的 AI 请求队列，统一以 agent 模式处理请求。
 *
 * 数据流：
 *   前端(匿名) --写--> ai_requests {mode, question, status:pending}
 *   本 worker(API Key) --轮询--> ai_requests --处理--> ai_replies {request_id, text, done}
 *   前端 --轮询--> ai_replies --拼字--> 显示
 *
 * 所有请求统一走 agent：spawn pi RPC，带 skill，能写 Python 跑计算/回测。
 * fast 直调模式已移除，历史遗留的 mode:'fast' 请求也按 agent 处理。
 */
'use strict';

const { spawn } = require('child_process');
const { StringDecoder } = require('string_decoder');
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;
const ENV = process.env.TCB_ENV || 'ljx-d1gjpcu23fa094e67';
const API_KEY = process.env.CB_API_KEY || '';
const POLL_MS = parseInt(process.env.POLL_MS || '1500', 10);
const DB_BASE = process.env.LOCAL_DB_BASE || 'http://127.0.0.1:8791';

function loadDotEnv(file) {
  try {
    for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
      const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
      if (m && process.env[m[1]] === undefined) process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
    }
  } catch {}
}
loadDotEnv(path.join(ROOT, '..', '.env')); // 密钥统一放仓库根 .env（见 ai/.env.example）
const KEY = process.env.CB_API_KEY || API_KEY;

// ---------- 数据库网关 HTTP API（同源数据服务；LOCAL_DB_BASE 指向远程旧网关时走 Bearer） ----------
async function cb(pathname, method = 'GET', body) {
  const r = await fetch(DB_BASE + pathname, {
    method,
    headers: DB_BASE.startsWith('http://127.0.0.1') || DB_BASE.startsWith('http://localhost')
      ? { 'Content-Type': 'application/json' }
      : { 'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`${method} ${pathname}: HTTP ${r.status} ${JSON.stringify(j).slice(0, 200)}`);
  return j;
}

async function getPending() {
  const q = encodeURIComponent(JSON.stringify({ status: 'pending' }));
  return (await cb(`/collections/ai_requests/documents?query=${q}&limit=5&order=${encodeURIComponent(JSON.stringify([{ field: 'created_at', direction: 'asc' }]))}`)).list || [];
}

async function setStatus(id, status) {
  await cb(`/collections/ai_requests/documents/${id}`, 'PATCH', {
    data: { $set: { status } },
  });
}

async function appendReply(reqId, text, thinking, done) {
  // 更新或插入回复
  const q = encodeURIComponent(JSON.stringify({ request_id: reqId }));
  const found = await cb(`/collections/ai_replies/documents?query=${q}&limit=1`);
  if (found.list && found.list.length) {
    const id = found.list[0]._id;
    await cb(`/collections/ai_replies/documents/${id}`, 'PATCH', {
      data: { $set: { text, thinking: thinking || '', done, updated_at: Date.now() } },
    });
  } else {
    await cb(`/collections/ai_replies/documents`, 'POST', {
      data: [{ request_id: reqId, text, thinking: thinking || '', done, created_at: Date.now(), updated_at: Date.now() }],
    });
  }
}

// ---------- Agent 模式：pi RPC（按 session_id 常驻，支持多轮） ----------
// 会话直接落在 pi 的默认会话目录 ~/.pi/agent/sessions/<session_id>/ 下，
// 这样本地终端跑 pi → /resume 也能看到网页产生的会话并继续对话。
const SESSION_DIR = path.join(require('os').homedir(), '.pi', 'agent', 'sessions');
const SESSION_IDLE_MS = 30 * 60 * 1000; // 会话闲置 30 分钟回收（pi 已自动落盘，可恢复）
const agentSessions = new Map(); // session_id -> {proc, busy, lastActive}
const activeRequests = new Map(); // request_id -> {abort()} 供 stop 请求中止

fs.mkdirSync(SESSION_DIR, { recursive: true });

// 给网页会话命名（写 session_info），让本地 /resume 列表可识别；
// 只在新会话没有名字时写，恢复旧会话不覆盖用户改过的名字
function nameSessionFile(sessionDir, name) {
  let attempts = 0;
  const timer = setInterval(() => {
    const file = latestSessionFile(sessionDir);
    if (file) {
      clearInterval(timer);
      const hasName = fs.existsSync(file) && fs.readFileSync(file, 'utf8').includes('"session_info"');
      if (!hasName) {
        try {
          const id = 'sn' + Date.now() + Math.random().toString(36).slice(2, 8);
          fs.appendFileSync(file, JSON.stringify({
            type: 'session_info', id, parentId: 'root',
            timestamp: new Date().toISOString(), name,
          }) + '\n');
        } catch (e) { console.error('命名会话失败:', e.message); }
      }
    } else if (++attempts > 15) {
      clearInterval(timer);
    }
  }, 1000);
}

function spawnPi(model, sessionDir, skillPath) {
  const args = [
    '--mode', 'rpc',
    '-e', path.join(ROOT, 'extensions/providers.js'),
    '--provider', model.split('/')[0], '--model', model.split('/')[1],
    '--session-dir', sessionDir,
  ];
  const env = { ...process.env };
  if (skillPath) {
    // 全部 skill（15 专家 + stock-data + web-search）对 agent 常驻可见、由它按任务自选；
    // 用户指定专家时只注入主身份提示（见 extensions/providers.js 的 before_agent_start）
    env.SKILL_IDENTITY =
      `[主专家] 用户在本站点指定了「${skillPath}」专家 skill：以其方法论与输出标准为主线完成需求；` +
      `同时你能看到全部可用 skill，可按任务需要取用其他 skill 辅助；` +
      `除非被直接询问，不要罗列你有哪些 skill。`;
  }
  const proc = spawn('pi', args, { cwd: ROOT, env, stdio: ['pipe', 'pipe', 'inherit'] });
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
  proc.on('exit', (code) => {
    for (const [sid, s] of agentSessions) if (s.proc === proc) s.dead = code;
  });
  return proc;
}

// 找到某个会话目录里最新的 .jsonl 会话文件（用于恢复）
function latestSessionFile(sessionDir) {
  try {
    const files = fs.readdirSync(sessionDir).filter(f => f.endsWith('.jsonl')).sort();
    return files.length ? path.join(sessionDir, files[files.length - 1]) : null;
  } catch { return null; }
}

function getAgentSession(sessionId, model, skillPath, nameHint) {
  model = model || 'kimi/k3';
  skillPath = skillPath || '';
  const sessionDir = path.join(SESSION_DIR, sessionId);
  let s = agentSessions.get(sessionId);
  if (s && !s.dead && s.model === model && s.skill === skillPath) { s.lastActive = Date.now(); return s; }
  if (s) { try { s.proc.kill(); } catch {} agentSessions.delete(sessionId); }
  const proc = spawnPi(model, sessionDir, skillPath);
  // pi 启动时若 --session-dir 里已有会话文件会自动加载最新会话（恢复多轮），
  // 不要再手动发 switch_session：实测"手动恢复 + 立即 prompt"会让 pi 卡死。
  // 仅给新会话命名（用首条提问，方便终端 /resume 辨识），恢复旧会话时不覆盖。
  proc.on('spawn', () => {
    nameSessionFile(sessionDir, nameHint || `StockDesk 网页会话 ${sessionId.slice(0, 13)}`);
  });
  s = { proc, model, skill: skillPath, busy: false, lastActive: Date.now(), dead: false };
  agentSessions.set(sessionId, s);
  return s;
}

async function handleAgent(reqId, question, model, sessionId, skillPath) {
  sessionId = sessionId || 'default';
  // 会话名取首条提问（压成单行、截断），让终端 /resume 列表一眼可辨
  const nameHint = '网页: ' + String(question || '').replace(/\s+/g, ' ').trim().slice(0, 30);
  const s = getAgentSession(sessionId, model, skillPath, nameHint);
  if (s.dead) { try { s.proc.kill(); } catch {} agentSessions.delete(sessionId); throw new Error('Agent 会话异常，请重试'); }
  let text = '';
  let thinking = '';
  let lastFlush = 0;
  let finished = false;
  let stopped = false;
  // 串行写队列：保证分块写(flush)在最终写(done=true)之前全部完成，避免并发覆盖
  let writeChain = Promise.resolve();
  const enqueueWrite = (fn) => { writeChain = writeChain.then(fn).catch((e) => console.error('写回复失败:', e.message)); };
  const flushNow = () => enqueueWrite(async () => { await appendReply(reqId, text, thinking, false); });
  // 停止：向 pi 发 abort（优雅取消当前回合，会话保留可续）；5 秒兜底强杀
  const abort = () => {
    stopped = true;
    try { s.proc.stdin.write(JSON.stringify({ id: 'abort' + Date.now(), type: 'abort' }) + '\n'); } catch {}
    setTimeout(() => { if (!finished) { try { s.proc.kill(); } catch {} } }, 5000);
  };
  activeRequests.set(reqId, { abort });
  const done = new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      try { s.proc.kill(); } catch {}
      s.dead = true;
      reject(new Error('Agent 超时（15分钟）'));
    }, 15 * 60 * 1000);
    s.proc.onLine = async (line) => {
      let e; try { e = JSON.parse(line); } catch { return; }
      if (e.type === 'message_update' && e.assistantMessageEvent) {
        const ev = e.assistantMessageEvent;
        if (ev.type === 'text_delta' && !finished) {
          text += ev.delta;
          const now = Date.now();
          if (now - lastFlush > 2000) { lastFlush = now; flushNow(); }
        } else if (ev.type === 'thinking_delta' && !finished) {
          thinking += ev.delta || '';
          const now = Date.now();
          if (now - lastFlush > 2000) { lastFlush = now; flushNow(); }
        }
      } else if (e.type === 'agent_end') {
        clearTimeout(timer);
        resolve();
      } else if (e.type === 'error') {
        // RPC error 事件（如模型不可用、prompt 预处理失败）——立即报错，避免挂死
        clearTimeout(timer);
        reject(new Error(typeof e.error === 'string' ? e.error : JSON.stringify(e).slice(0, 300)));
      } else if (e.type === 'message_update' && e.assistantMessageEvent && e.assistantMessageEvent.type === 'error') {
        clearTimeout(timer);
        reject(new Error(e.assistantMessageEvent.error?.errorMessage || 'Agent 错误'));
      }
    };
    s.proc.on('exit', (code) => { if (!timer._destroyed) { clearTimeout(timer); } resolve(); });
    s.busy = true;
    s.lastActive = Date.now();
    s.msgId = (s.msgId || 0) + 1;
    s.proc.stdin.write(JSON.stringify({ id: 'p' + s.msgId, type: 'prompt', message: question }) + '\n');
  });
  await done;
  finished = true;
  activeRequests.delete(reqId);
  // 等所有分块写完成，最后写 done=true
  const finalText = (text || '(无回复)') + (stopped ? '\n\n[已由用户停止]' : '');
  await enqueueWrite(async () => { await appendReply(reqId, finalText, thinking, true); });
  await writeChain;
  s.busy = false;
  s.lastActive = Date.now();
}

// 闲置会话回收
setInterval(() => {
  const now = Date.now();
  for (const [sid, s] of agentSessions) {
    if (!s.busy && now - s.lastActive > SESSION_IDLE_MS) {
      console.log(`回收闲置会话 ${sid}`);
      try { s.proc.kill(); } catch {}
      agentSessions.delete(sid);
    }
  }
}, 60000);

// ---------- 主循环 ----------
async function tick() {
  let reqs = [];
  try { reqs = await getPending(); } catch (e) { console.error('轮询失败:', e.message); return; }
  for (const r of reqs) {
    const id = r._id;
    // 停止请求：abort 正在处理的目标；目标尚未开始则标记 cancelled
    if (r.mode === 'stop') {
      const t = r.target_id;
      const act = t && activeRequests.get(t);
      if (act) {
        act.abort();
        console.log(`停止请求 ${id} -> abort target ${t}`);
      } else {
        console.log(`停止请求 ${id} -> target ${t} 未在处理中`);
        if (t) { try { await setStatus(t, 'cancelled'); } catch {} }
      }
      try { await setStatus(id, 'done'); } catch {}
      continue;
    }
    console.log(`处理请求 ${id} mode=${r.mode}`);
    try {
      await setStatus(id, 'processing');
      // 统一 agent 模式：不看 mode 值（历史遗留的 fast 请求也按 agent 处理）
      await handleAgent(id, r.question, r.model, r.session_id, r.skill);
      await setStatus(id, 'done');
      console.log(`完成 ${id}`);
    } catch (e) {
      console.error(`请求 ${id} 失败:`, e.message);
      try { await appendReply(id, '处理失败：' + e.message, '', true); } catch {}
      try { await setStatus(id, 'error'); } catch {}
    }
  }
}

console.log(`StockDesk worker 启动 env=${ENV} poll=${POLL_MS}ms key=${KEY ? 'set' : 'MISSING'}`);
setInterval(tick, POLL_MS);
tick();
