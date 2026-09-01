/**
 * StockDesk AI Worker — 出站轮询 CloudBase，处理 fast/agent 双模式请求。
 *
 * 数据流：
 *   前端(匿名) --写--> ai_requests {mode, question, status:pending}
 *   本 worker(API Key) --轮询--> ai_requests --处理--> ai_replies {request_id, text, done}
 *   前端 --轮询--> ai_replies --拼字--> 显示
 *
 * fast  : 直接调模型 API + 简单工具（stock_api.py 查数据），快、便宜
 * agent : spawn pi RPC，带 skill，能写 Python 跑计算/回测
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
const DB_BASE = `https://${ENV}.api.tcloudbasegateway.com/v1/database/instances/(default)/databases/(default)`;

function loadDotEnv(file) {
  try {
    for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
      const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
      if (m && process.env[m[1]] === undefined) process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
    }
  } catch {}
}
loadDotEnv(path.join(ROOT, '.env'));
const KEY = process.env.CB_API_KEY || API_KEY;

// ---------- CloudBase NoSQL HTTP API ----------
async function cb(pathname, method = 'GET', body) {
  const r = await fetch(DB_BASE + pathname, {
    method,
    headers: { 'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json' },
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

// ---------- 模型调用（fast 模式，OpenAI 兼容） ----------
const MODELS = {
  'kimi/k3': { base: 'https://api.kimi.com/coding/v1', key: process.env.KIMI_API_KEY, model: 'k3' },
  'oczen/deepseek-v4-flash': { base: 'https://opencode.ai/zen/go/v1', key: process.env.OPENCODE_API_KEY, model: 'deepseek-v4-flash' },
  'oczen/deepseek-v4-flash-vision-exp': { base: 'https://opencode.ai/zen/go/v1', key: process.env.OPENCODE_API_KEY, model: 'deepseek-v4-flash-vision-exp' },
};

async function chatOnce(modelKey, messages, tools) {
  const m = MODELS[modelKey] || MODELS['kimi/k3'];
  const r = await fetch(m.base + '/chat/completions', {
    method: 'POST',
    headers: { 'Authorization': 'Bearer ' + m.key, 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: m.model, messages, tools, max_tokens: 2000 }),
  });
  const j = await r.json();
  if (!r.ok) throw new Error(`模型API ${r.status}: ${JSON.stringify(j).slice(0, 200)}`);
  return j.choices && j.choices[0] ? j.choices[0].message : {};
}

// 流式调用（fast 模式）：支持 thinking 推送 + AbortController 停止
// onContent / onThinking 在每段增量到达时回调（调用方负责节流落盘）
async function chatStream(modelKey, messages, tools, signal, onContent, onThinking) {
  const m = MODELS[modelKey] || MODELS['kimi/k3'];
  const r = await fetch(m.base + '/chat/completions', {
    method: 'POST',
    headers: { 'Authorization': 'Bearer ' + m.key, 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: m.model, messages, tools, stream: true, max_tokens: 2000 }),
    signal,
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(`模型API ${r.status}: ${JSON.stringify(j).slice(0, 200)}`);
  }
  const reader = r.body.getReader();
  const dec = new StringDecoder('utf8');
  let buf = '';
  let content = '';
  let reasoning = '';
  let finish = null;
  const byIndex = new Map(); // 流式 tool_calls 按 index 累积
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.write(value);
    let i;
    while ((i = buf.indexOf('\n')) !== -1) {
      const line = buf.slice(0, i); buf = buf.slice(i + 1);
      const s = line.trim();
      if (!s.startsWith('data:')) continue;
      const payload = s.slice(5).trim();
      if (payload === '[DONE]') { finish = true; break; }
      let c; try { c = JSON.parse(payload); } catch { continue; }
      const ch = c.choices && c.choices[0];
      if (!ch) continue;
      if (ch.finish_reason) finish = ch.finish_reason;
      const d = ch.delta || {};
      if (d.content) { content += d.content; onContent(d.content); }
      if (d.reasoning_content) { reasoning += d.reasoning_content; onThinking(d.reasoning_content); }
      if (d.tool_calls) {
        for (const tc of d.tool_calls) {
          let acc = byIndex.get(tc.index) || { id: '', name: '', args: '' };
          if (tc.id) acc.id = tc.id;
          if (tc.function) {
            if (tc.function.name) acc.name += tc.function.name;
            if (tc.function.arguments) acc.args += tc.function.arguments;
          }
          byIndex.set(tc.index, acc);
        }
      }
    }
    if (finish === true) break;
  }
  const toolCalls = [...byIndex.values()]
    .filter((tc) => tc.name)
    .map((tc) => ({ id: tc.id, type: 'function', function: { name: tc.name, arguments: tc.args } }));
  return { content, reasoning, toolCalls };
}

// 简单工具：股票数据查询（fast 模式给模型用）
const TOOLS = [
  {
    type: 'function',
    function: {
      name: 'stock_query',
      description: '查询 A 股行情数据。realtime=实时价/涨跌幅，daily=历史日线，stocks=自选股目录，signals=最近信号',
      parameters: {
        type: 'object',
        properties: {
          cmd: { type: 'string', enum: ['realtime', 'daily', 'stocks', 'signals'] },
          symbols: { type: 'string', description: '6位代码，逗号分隔；realtime 可省略用自选股' },
          limit: { type: 'integer', description: 'daily 的条数，默认120' },
        },
        required: ['cmd'],
      },
    },
  },
];

const SCRIPT = path.join(ROOT, 'skill', 'stock-data', 'scripts', 'stock_api.py');
function runStockTool(args) {
  return new Promise((resolve) => {
    let cmd;
    if (args.cmd === 'realtime') cmd = `python3 ${SCRIPT} realtime ${args.symbols || ''}`.trim();
    else if (args.cmd === 'daily') cmd = `python3 ${SCRIPT} daily ${args.symbols || ''} --limit ${args.limit || 120}`;
    else if (args.cmd === 'stocks') cmd = `python3 ${SCRIPT} stocks`;
    else if (args.cmd === 'signals') cmd = `python3 ${SCRIPT} signals --limit ${args.limit || 20}`;
    else return resolve('未知命令');
    require('child_process').exec(cmd, { timeout: 20000 }, (e, stdout, stderr) => {
      if (e) return resolve('执行失败: ' + (stderr || e.message).slice(0, 500));
      resolve(stdout.slice(0, 3000));
    });
  });
}

async function handleFast(reqId, question, model) {
  model = model || 'kimi/k3';
  const messages = [
    { role: 'system', content: '你是 StockDesk 行情台内置的股票研究助理，用简体中文简洁回答。涉及股票数字必须调用 stock_query 取真实数据，禁止凭记忆估算。' },
    { role: 'user', content: question },
  ];
  let text = '';
  let thinking = '';
  let lastFlush = 0;
  let stopped = false;
  const ac = new AbortController();
  let writeChain = Promise.resolve();
  const enqueueWrite = (fn) => { writeChain = writeChain.then(fn).catch((e) => console.error('写回复失败:', e.message)); };
  const flushNow = () => enqueueWrite(async () => { await appendReply(reqId, text, thinking, false); });
  const throttleFlush = () => { const now = Date.now(); if (now - lastFlush > 2000) { lastFlush = now; flushNow(); } };
  const abort = () => { stopped = true; ac.abort(); };
  activeRequests.set(reqId, { abort });

  try {
    for (let round = 0; round < 4; round++) {
      const { content, reasoning, toolCalls } = await chatStream(
        model, messages, TOOLS, ac.signal,
        (d) => { text += d; throttleFlush(); },
        (t) => { thinking += t; throttleFlush(); },
      );
      if (stopped) break;
      // 有工具调用则执行取数后继续下一轮
      if (toolCalls && toolCalls.length) {
        messages.push({ role: 'assistant', content: content || '', tool_calls: toolCalls });
        for (const tc of toolCalls) {
          let args = {}; try { args = JSON.parse(tc.function.arguments || '{}'); } catch {}
          const result = await runStockTool(args);
          messages.push({ role: 'tool', tool_call_id: tc.id, content: result });
        }
        continue;
      }
      break;
    }
  } catch (e) {
    if (e.name === 'AbortError') stopped = true;
    else throw e;
  }
  activeRequests.delete(reqId);
  const finalText = (text || '(无回复)') + (stopped ? '\n\n[已由用户停止]' : '');
  await enqueueWrite(async () => { await appendReply(reqId, finalText, thinking, true); });
  await writeChain;
}

// ---------- Agent 模式：pi RPC（按 session_id 常驻，支持多轮） ----------
// 会话直接落在 pi 的默认会话目录 ~/.pi/agent/sessions/<session_id>/ 下，
// 这样本地终端跑 pi → /resume 也能看到网页产生的会话并继续对话。
const SESSION_DIR = path.join(require('os').homedir(), '.pi', 'agent', 'sessions');
const SESSION_IDLE_MS = 30 * 60 * 1000; // 会话闲置 30 分钟回收（pi 已自动落盘，可恢复）
const SKILL_BASE = process.env.SKILL_BASE || path.join(ROOT, 'skill'); // 专家 skill 目录，可用 SKILL_BASE 环境变量覆盖
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
    // 用户选择了专家 skill：隔离默认 skills，只加载所选专家 skill + stock-data（基础取数），
    // 并注入专家身份引导（见 extensions/providers.js 的 before_agent_start）
    args.push('--no-skills');
    args.push('--skill', path.join(SKILL_BASE, skillPath));
    args.push('--skill', path.join(ROOT, '.pi/skills/stock-data'));
    env.SKILL_IDENTITY =
      `[专家身份] 用户在本站点选择了「${skillPath}」专家 skill，你以该领域资深专家的身份工作。` +
      `主动调用该 skill（SKILL.md）提供的方法与脚本完成用户需求，严格遵循其中的规则与输出标准；` +
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

function getAgentSession(sessionId, model, skillPath) {
  model = model || 'kimi/k3';
  skillPath = skillPath || '';
  const sessionDir = path.join(SESSION_DIR, sessionId);
  let s = agentSessions.get(sessionId);
  if (s && !s.dead && s.model === model && s.skill === skillPath) { s.lastActive = Date.now(); return s; }
  if (s) { try { s.proc.kill(); } catch {} agentSessions.delete(sessionId); }
  const proc = spawnPi(model, sessionDir, skillPath);
  // pi 启动时若 --session-dir 里已有会话文件会自动加载最新会话（恢复多轮），
  // 不要再手动发 switch_session：实测"手动恢复 + 立即 prompt"会让 pi 卡死。
  // 仅给新会话命名，便于本地 /resume 识别。
  proc.on('spawn', () => {
    nameSessionFile(sessionDir, `StockDesk 网页会话 ${sessionId.slice(0, 13)}`);
  });
  s = { proc, model, skill: skillPath, busy: false, lastActive: Date.now(), dead: false };
  agentSessions.set(sessionId, s);
  return s;
}

async function handleAgent(reqId, question, model, sessionId, skillPath) {
  sessionId = sessionId || 'default';
  const s = getAgentSession(sessionId, model, skillPath);
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
      if (r.mode === 'agent') {
        await handleAgent(id, r.question, r.model, r.session_id, r.skill);
      } else {
        await handleFast(id, r.question, r.model);
      }
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
