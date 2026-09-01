/* StockDesk AI 对话框 v2 —— IIFE 封装，避免与 app.js 顶层声明冲突 */
(() => {
/* StockDesk AI 对话框 v2 —— 走 CloudBase 中转（匿名写请求/读回复，A 机出站处理） */

const AI_ENV = 'ljx-d1gjpcu23fa094e67';
const AI_PKEY = 'eyJhbGciOiJSUzI1NiIsImtpZCI6IjlkMWRjMzFlLWI0ZDAtNDQ4Yi1hNzZmLWIwY2M2M2Q4MTQ5OCJ9.eyJpc3MiOiJodHRwczovL2xqeC1kMWdqcGN1MjNmYTA5NGU2Ny5hcC1zaGFuZ2hhaS50Y2ItYXBpLnRlbmNlbnRjbG91ZGFwaS5jb20iLCJzdWIiOiJhbm9uIiwiYXVkIjoibGp4LWQxZ2pwY3UyM2ZhMDk0ZTY3IiwiZXhwIjo0MDkxOTI5MDkxLCJpYXQiOjE3ODgyNDU4OTEsIm5vbmNlIjoiakk0bnNqS3NUODJaX0IwOXBnMTZmdyIsImF0X2hhc2giOiJqSTRuc2pLc1Q4MlpfQjA5cGcxNmZ3IiwibmFtZSI6IkFub255bW91cyIsInNjb3BlIjoiYW5vbnltb3VzIiwicHJvamVjdF9pZCI6ImxqeC1kMWdqcGN1MjNmYTA5NGU2NyIsIm1ldGEiOnsicGxhdGZvcm0iOiJQdWJsaXNoYWJsZUtleSJ9LCJ1c2VyX3R5cGUiOiIiLCJjbGllbnRfdHlwZSI6ImNsaWVudF91c2VyIiwiaXNfc3lzdGVtX2FkbWluIjpmYWxzZX0.Y2CDPK_3zxplL8NoTvWuE3F3zQATQe7CFBgHN2CEnUHqGE-_Xbl3n6dfDsBkxIeoMrmR8J42r_0-geOj-T_svLw8T5xh-xv5gmoOGzBh_G4moFEdinxgTQ2g1-uQYHNWDMmEh9lg4ZWxmKXBoA54ex7DxxCkOwy7wPRHw8joRNEyVYYMUwD05XJXraQsM3VkUmqe8gQwCGM1A0RRBsFn2ShBAiHRa9KjkeV0Vze1xajthoS57V9t9C64F6nFYNhaU-_4_dvmZZcq-UwLKNPLfsBO0RL2gnMkkidaePb2pEmT2LLgRkvWzAo_fdR95o_GO11V5VrKu0g4xwU4r_wvKA';
const AI_DB = `https://${AI_ENV}.api.tcloudbasegateway.com/v1/database/instances/(default)/databases/(default)`;

const ai = {
  // session_id：一次持久对话（localStorage），agent 模式靠它续上下文
  sid: localStorage.getItem('stockdesk_ai_sid') || (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()),
  mode: localStorage.getItem('stockdesk_ai_mode') || 'fast',
  model: localStorage.getItem('stockdesk_ai_model') || 'kimi/k3',
  skill: localStorage.getItem('stockdesk_ai_skill') || '',
  activeReq: null,       // 当前进行中的请求 id
  activeTimer: null,     // 轮询定时器
  msgs: [],              // {role, text}
};

localStorage.setItem('stockdesk_ai_sid', ai.sid);

async function aiDb(path, opts = {}) {
  const r = await fetch(AI_DB + path, {
    ...opts,
    headers: { 'Authorization': 'Bearer ' + AI_PKEY, 'Content-Type': 'application/json', ...opts.headers },
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.message || `HTTP ${r.status}`);
  return j;
}

function aiToast(msg, isErr) {
  const d = document.createElement('div');
  d.className = 'ai-toast' + (isErr ? ' ai-toast-err' : '');
  d.textContent = msg;
  document.body.appendChild(d);
  setTimeout(() => d.remove(), 4000);
}

function openAi() {
  document.getElementById('ai').classList.add('show');
  document.body.style.overflow = 'hidden';
  renderAi();
}
function closeAi() {
  document.getElementById('ai').classList.remove('show');
  document.body.style.overflow = '';
  if (ai.activeTimer) { clearInterval(ai.activeTimer); ai.activeTimer = null; }
}

function renderAi() {
  const box = document.getElementById('ai-msgs');
  box.innerHTML = ai.msgs.length ? '' : `<div class="ai-empty">
    我是你的股票研究助理，两种模式：<br><br>
    ⚡ <b>快速模式</b>：直接问答 + 查数据，1~2 秒出结果，便宜<br>
    🤖 <b>Agent 模式</b>：完整 agent，能写代码算指标/回测，支持多轮记忆<br><br>
    选择模式后开始提问 ↓
  </div>`;
  for (const m of ai.msgs) box.appendChild(msgEl(m));
  box.scrollTop = box.scrollHeight;
}

function msgEl(m) {
  const d = document.createElement('div');
  d.className = 'ai-msg ' + m.role;
  const think = m.thinking ? `<details class="ai-thinking" open><summary>🤔 思考过程</summary><div class="ai-think-body">${esc(m.thinking)}</div></details>` : '';
  d.innerHTML = `${think}<div class="ai-bubble">${linkify(esc(m.text)).replace(/\n/g, '<br>')}${m.pending ? '<span class="ai-typing">…</span>' : ''}</div>`;
  return d;
}
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const linkify = s => s.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');

function bindAi() {
  document.getElementById('ai-fab').onclick = openAi;
  document.getElementById('ai-close').onclick = closeAi;
  document.getElementById('ai-mask').onclick = closeAi;
  document.getElementById('ai-send').onclick = sendMsg;
  document.getElementById('ai-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMsg(); }
  });
  document.getElementById('ai-mode-fast').onclick = () => setMode('fast');
  document.getElementById('ai-mode-agent').onclick = () => setMode('agent');
  document.getElementById('ai-model').onchange = e => { ai.model = e.target.value; localStorage.setItem('stockdesk_ai_model', ai.model); };
  document.getElementById('ai-skill').onchange = e => { ai.skill = e.target.value; localStorage.setItem('stockdesk_ai_skill', ai.skill); };
  document.getElementById('ai-stop').onclick = stopCurrent;
  document.getElementById('ai-clear').onclick = clearChat;
  document.getElementById('ai-input').placeholder = ai.mode === 'agent' ? 'Agent 模式：可写代码算指标/回测，支持多轮…' : '快速模式：问行情/查数据…';
}

function setMode(m) {
  ai.mode = m;
  localStorage.setItem('stockdesk_ai_mode', m);
  document.getElementById('ai-mode-fast').classList.toggle('active', m === 'fast');
  document.getElementById('ai-mode-agent').classList.toggle('active', m === 'agent');
  document.getElementById('ai-input').placeholder = m === 'agent' ? 'Agent 模式：可写代码算指标/回测，支持多轮…' : '快速模式：问行情/查数据…';
}

async function sendMsg() {
  if (ai.activeReq) { aiToast('上一条还在处理中…'); return; }
  const inp = document.getElementById('ai-input');
  const text = inp.value.trim();
  if (!text) return;
  inp.value = '';
  ai.msgs.push({ role: 'user', text });
  renderAi();
  // 写请求
  try {
    const j = await aiDb('/collections/ai_requests/documents', {
      method: 'POST',
      body: JSON.stringify({
        data: [{
          mode: ai.mode, question: text, session_id: ai.sid, status: 'pending',
          model: ai.model, skill: ai.mode === 'agent' ? ai.skill : '',
          created_at: { $date: { $numberLong: String(Date.now()) } },
        }],
      }),
    });
    ai.activeReq = j.insertedIds[0];
    ai.msgs.push({ role: 'assistant', text: '', thinking: '', pending: true });
    setBusy(true);
    renderAi();
    pollReply();
  } catch (e) {
    aiToast('提交失败: ' + e.message, true);
  }
}

// 请求处理中切换 发送/停止 按钮
function setBusy(busy) {
  const send = document.getElementById('ai-send');
  const stop = document.getElementById('ai-stop');
  send.style.display = busy ? 'none' : '';
  stop.style.display = busy ? '' : 'none';
}

// 用户点击停止：写一条 stop 请求，worker 轮询到后 abort 当前生成
async function stopCurrent() {
  if (!ai.activeReq) return;
  try {
    await aiDb('/collections/ai_requests/documents', {
      method: 'POST',
      body: JSON.stringify({
        data: [{
          mode: 'stop', target_id: ai.activeReq, status: 'pending',
          created_at: { $date: { $numberLong: String(Date.now()) } },
        }],
      }),
    });
    aiToast('已发送停止指令，正在停止…');
  } catch (e) {
    aiToast('停止指令发送失败: ' + e.message, true);
  }
}

function pollReply() {
  if (!ai.activeReq) return;
  if (ai.activeTimer) clearInterval(ai.activeTimer);
  const box = document.getElementById('ai-msgs');
  ai.activeTimer = setInterval(async () => {
    try {
      const q = encodeURIComponent(JSON.stringify({ request_id: ai.activeReq }));
      const j = await aiDb(`/collections/ai_replies/documents?query=${q}&limit=1`);
      const rep = j.list && j.list[0];
      if (!rep) return; // 还没被处理
      const idx = ai.msgs.findIndex(m => m.pending);
      if (idx >= 0) {
        ai.msgs[idx].text = rep.text || '';
        ai.msgs[idx].thinking = rep.thinking || '';
        ai.msgs[idx].pending = !rep.done;
        // 替换当前节点
        const node = box.children[idx];
        if (node) node.outerHTML = msgEl(ai.msgs[idx]).outerHTML;
        box.scrollTop = box.scrollHeight;
      }
      if (rep.done) {
        clearInterval(ai.activeTimer);
        ai.activeTimer = null;
        ai.activeReq = null;
        setBusy(false);
        renderAi();
      }
    } catch (e) {
      // 网络抖动忽略，下次轮询重试
    }
  }, 2000);
}

async function clearChat() {
  if (!confirm('清空当前对话？Agent 模式的记忆也会清除。')) return;
  if (ai.activeTimer) { clearInterval(ai.activeTimer); ai.activeTimer = null; }
  ai.sid = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random();
  localStorage.setItem('stockdesk_ai_sid', ai.sid);
  ai.msgs = [];
  ai.activeReq = null;
  setBusy(false);
  renderAi();
}

document.addEventListener('DOMContentLoaded', () => {
  bindAi();
  setMode(ai.mode);
});

})();
