// 全局状态与 API
const API = 'https://ljx-d1gjpcu23fa094e67.service.tcloudbase.com/api';
const store = {
  dir: [],           // stocks 集合目录
  snaps: {},         // symbol -> snapshot
  page: 'watch',     // 当前页面
  tab: 'all',        // watchlist tab
  kind: 'stock',     // index page tab
  favs: new Set(JSON.parse(localStorage.getItem('stockdesk_favs') || '[]')),
  detail: null,      // 详情页 symbol
  chartRange: 250,
  chart: null,       // echarts 实例
  chartReq: 0,       // 图表请求序号（竞态守卫）
  chartType: 'daily', // daily | hour | tick
  profileChart: null,
  profileReq: 0,     // 量能分布请求序号
  profiles: {},      // symbol -> 量能分布缓存
  signalsList: [],   // 信号页列表缓存（点击定位用）
  markSignal: null,  // 详情页待标记的信号
  signalHist: null,  // symbol -> 该标的信号历史（缓存）
  showAllSignals: false, // 是否显示全部历史信号标记
};
const saveFavs = () => localStorage.setItem('stockdesk_favs', JSON.stringify([...store.favs]));

async function api(path) {
  const r = await fetch(API + path);
  let j;
  try { j = await r.json(); } catch { throw new Error(`接口响应异常 (HTTP ${r.status})`); }
  if (j.error) throw new Error(j.error);
  return j.data;
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtQuoteTime = (qt) => {
  if (!qt) return '-';
  const c = qt.indexOf(':'); // 新浪格式 '2026-08-3116:14:55'
  if (c >= 0) return qt.slice(c - 2, c + 3);
  return qt.slice(8, 10) + ':' + qt.slice(10, 12); // 腾讯格式 '20260831161455'
};

const fmtPct = (v) => (v == null ? '-' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%');
const cls = (v) => (v == null ? '' : v > 0 ? 'up' : v < 0 ? 'down' : 'flat');
const fmtVol = (h) => h >= 1e4 ? (h / 1e4).toFixed(1) + '万手' : h + '手';
const fmtAmt = (w) => w >= 1e4 ? (w / 1e4).toFixed(2) + '亿' : w + '万';
const fmtMv = (v) => v && v > 0 ? v.toFixed(1) + '亿' : '-';
const fmtPe = (v) => v && v > 0 ? v.toFixed(1) : '-';
const ruleText = (r) => r.startsWith('daily:') ? '日线' : r.endsWith('_hist') ? '历史回放' : '盘中';

// 详情页行情头部（每个术语带 ⓘ 解释入口）
function quoteGridHtml(q) {
  return `
    <div class="q-price ${cls(q.pct_chg)}">${q.price.toFixed(2)} <span style="font-size:14px">${fmtPct(q.pct_chg)}<i class="help-ico" data-help="pct">ⓘ</i></span></div>
    <div class="q-grid">
      <div class="q-item"><span>今开<i class="help-ico" data-help="open">ⓘ</i></span>${q.open.toFixed(2)}</div>
      <div class="q-item"><span>昨收<i class="help-ico" data-help="prev_close">ⓘ</i></span>${q.prev_close.toFixed(2)}</div>
      <div class="q-item"><span>最高<i class="help-ico" data-help="high">ⓘ</i></span>${q.high.toFixed(2)}</div>
      <div class="q-item"><span>最低<i class="help-ico" data-help="low">ⓘ</i></span>${q.low.toFixed(2)}</div>
      <div class="q-item"><span>成交量<i class="help-ico" data-help="volume">ⓘ</i></span>${fmtVol(q.volume_hand)}</div>
      <div class="q-item"><span>成交额<i class="help-ico" data-help="amount">ⓘ</i></span>${fmtAmt(q.amount_wan)}</div>
      <div class="q-item"><span>换手率<i class="help-ico" data-help="turnover">ⓘ</i></span>${q.turnover_rate}%</div>
      <div class="q-item"><span>更新<i class="help-ico" data-help="time">ⓘ</i></span>${fmtQuoteTime(q.quote_time)}</div>
      <div class="q-item"><span>总市值<i class="help-ico" data-help="total_mv">ⓘ</i></span>${fmtMv(q.total_mv)}</div>
      <div class="q-item"><span>流通市值<i class="help-ico" data-help="circ_mv">ⓘ</i></span>${fmtMv(q.circ_mv)}</div>
      <div class="q-item"><span>市盈率<i class="help-ico" data-help="pe_ttm">ⓘ</i></span>${fmtPe(q.pe_ttm)}</div>
    </div>`;
}

function findItem(sym) { return store.dir.find(d => d.symbol === sym); }
function snap(sym) { return store.snaps[sym]; }

async function refreshSnapshots() {
  const list = await api('/quote');
  store.snaps = Object.fromEntries(list.map(q => [q.symbol, q]));
}

// ---------- 页面：自选 ----------
function renderWatch() {
  const groups = { stock: [], index: [] };
  for (const d of store.dir) (groups[d.type] || []).push(d);
  const favItems = [...store.favs].map(findItem).filter(Boolean);
  const tab = store.tab;
  let items = [];
  if (tab === 'stock') items = groups.stock;
  else if (tab === 'index') items = groups.index;
  else if (tab === 'fav') items = favItems;
  else items = [...groups.stock, ...groups.index];

  const tabs = [['all', '全部'], ['stock', '个股'], ['index', '指数'], ['fav', `自选(${favItems.length})`]];
  const tabHtml = tabs.map(([k, t]) => `<span class="tab ${k === tab ? 'active' : ''}" data-tab="${k}">${t}</span>`).join('');

  const listHtml = items.length ? items.map(it => {
    const q = snap(it.symbol) || {};
    return `<div class="card" data-sym="${it.symbol}">
      <div><div class="name">${esc(it.name)}</div><div class="sub">${esc(it.symbol)}${it.type === 'index' ? ' · 指数' : ''}</div></div>
      <div class="num">${q.price != null ? q.price.toFixed(2) : '-'}</div>
      <div class="num ${cls(q.pct_chg)}">${fmtPct(q.pct_chg)}</div>
    </div>`;
  }).join('') : '<div class="empty">自选为空，点卡片进入详情后收藏</div>';

  document.getElementById('content').innerHTML = `<div class="tabs">${tabHtml}</div>${listHtml}`;
  document.querySelectorAll('#content .tab').forEach(el =>
    el.onclick = () => { store.tab = el.dataset.tab; renderWatch(); });
  document.querySelectorAll('#content .card').forEach(el =>
    el.onclick = () => showDetail(el.dataset.sym));
}

// ---------- 页面：指数 ----------
function renderIndex() {
  const all = store.dir.filter(d => d.type === 'index' || d.type === 'industry');
  const sh = all.filter(d => d.symbol.startsWith('sh'));
  const sz = all.filter(d => d.symbol.startsWith('sz'));
  const ind = all.filter(d => d.type === 'industry');
  const list = store.kind === 'industry' ? ind : (store.kind === 'stock' ? sh : sz);
  const tabs = [['stock', '沪市指数'], ['index', '深市指数'], ['industry', '行业']];
  const tabHtml = tabs.map(([k, t]) => `<span class="tab ${k === store.kind ? 'active' : ''}" data-kind="${k}">${t}</span>`).join('');
  const listHtml = list.map(it => {
    const q = snap(it.symbol) || {};
    return `<div class="card" data-sym="${it.symbol}">
      <div><div class="name">${esc(it.name)}</div><div class="sub">${esc(it.symbol)}</div></div>
      <div class="num">${q.price != null ? q.price.toFixed(2) : '-'}</div>
      <div class="num ${cls(q.pct_chg)}">${fmtPct(q.pct_chg)}</div>
    </div>`;
  }).join('');
  document.getElementById('content').innerHTML = `<div class="tabs">${tabHtml}</div>${listHtml}`;
  document.querySelectorAll('#content .tab').forEach(el =>
    el.onclick = () => { store.kind = el.dataset.kind; renderIndex(); });
  document.querySelectorAll('#content .card').forEach(el =>
    el.onclick = () => showDetail(el.dataset.sym));
}

// ---------- 页面：信号 ----------
async function renderSignals() {
  document.getElementById('content').innerHTML = '<div class="empty">加载中…</div>';
  const all = await api('/signals?limit=200');
  const realtime = all.filter(s => !s.hist);
  store.signalsList = realtime;
  const html = realtime.length ? realtime.map(s => {
    const t = new Date(s.ts * 1000);
    const ts = `${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')} ${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`;
    return `<div class="sig" data-sid="${s.id ?? s.ts}" data-sym="${esc(s.symbol)}"><div class="sig-meta">${ruleText(s.rule)} · ${ts} · ${esc(s.symbol)}</div><div class="sig-msg">${esc(s.message)}</div></div>`;
  }).join('') : '<div class="empty">暂无信号</div>';
  document.getElementById('content').innerHTML = `<div class="chart-help"><span class="chip" data-help="signal">信号是什么 ⓘ</span></div>` + html;
  document.querySelectorAll('#content .sig').forEach(el => {
    el.onclick = () => {
      const s = store.signalsList.find(x => String(x.id ?? x.ts) === el.dataset.sid);
      if (s) showDetail(s.symbol, s);
    };
  });
}

// ---------- 页面：我的 ----------
function renderMe() {
  const validFavs = [...store.favs].filter(sym => findItem(sym));
  const favHtml = validFavs.map(sym => {
    const it = findItem(sym);
    return `<div class="fav-row"><span>${esc(it.name)} <span class="sub">${esc(sym)}</span></span><span class="fav-rm" data-sym="${sym}">移除</span></div>`;
  }).join('') || '<div class="empty">暂无自选</div>';
  document.getElementById('content').innerHTML = `
    <div class="sec-title">我的自选（${validFavs.length}）</div>
    <div class="card" style="display:block">${favHtml}</div>
    <div class="sec-title">同步状态</div>
    <div class="card" style="display:block;padding:10px 14px">
      <div class="fav-row"><span>日线同步</span><span class="sub">交易日 15:45</span></div>
      <div class="fav-row"><span>快照同步</span><span class="sub">盘中每 5 分钟</span></div>
      <div class="fav-row"><span>信号同步</span><span class="sub">每小时</span></div>
    </div>`;
  document.querySelectorAll('.fav-rm').forEach(el =>
    el.onclick = () => { store.favs.delete(el.dataset.sym); saveFavs(); renderMe(); });
}

// ---------- 页面：宏观 ----------
const MACRO_CAT = {
  money: ['m2_yoy', 'm1_yoy'],
  infl: ['cpi_yoy', 'ppi_yoy'],
  growth: ['pmi_mfg', 'ip_yoy'],
  credit: ['social_financing'],
  rate: ['lpr_1y', 'shibor_3m', 'cn_gov10y', 'cn_ts10y2y', 'us_gov10y', 'us_ts10y2y'],
  funds: ['margin_sh', 'margin_sz'],
  overseas: ['fred_VIXCLS', 'fred_WALCL', 'fred_RRPONTSYD', 'fred_DTWEXBGS', 'fred_DFF'],
  emotion: ['qvix_300', 'qvix_1000'],
};
const MACRO_CAT_NAME = { money: '货币', infl: '通胀', growth: '增长', credit: '信用', rate: '利率', funds: '资金面', overseas: '海外', emotion: '情绪' };
const fmtMacroNum = (v) => {
  if (v == null) return '-';
  if (Math.abs(v) >= 10000) return Math.round(v).toLocaleString();
  if (Math.abs(v) >= 100) return Math.round(v * 100) / 100;
  return String(Math.round(v * 100) / 100);
};
const macroChg = (it) => {
  if (it.prev == null) return null;
  const d = it.latest - it.prev;
  return { d, txt: (d >= 0 ? '+' : '') + fmtMacroNum(Math.abs(d) < 0.005 ? Math.round(d * 1000) / 1000 : d) + (it.unit || '') };
};
const macroSparkOpt = (it) => ({
  grid: { left: 0, right: 0, top: 2, bottom: 0 },
  xAxis: { type: 'category', show: false, boundaryGap: false, data: it.series.map(p => p[0]) },
  yAxis: { type: 'value', show: false, scale: true },
  series: [{
    type: 'line', data: it.series.map(p => p[1]), showSymbol: false, smooth: true,
    lineStyle: { width: 1.5, color: '#4d8fd1' },
    areaStyle: { color: 'rgba(77,143,209,.14)' },
  }],
  animation: false,
});
let macroCharts = [];
async function renderMacro() {
  document.getElementById('content').innerHTML = '<div class="empty">加载中…</div>';
  const list = await api('/macro');
  store.macro = list;
  const used = new Set(Object.values(MACRO_CAT).flat());
  const byCat = [];
  for (const [cat, ids] of Object.entries(MACRO_CAT)) {
    const items = ids.map(id => list.find(x => x.id === id)).filter(Boolean);
    if (items.length) byCat.push([cat, items]);
  }
  const rest = list.filter(x => !used.has(x.id));
  if (rest.length) byCat.push(['other', rest]);
  let html = `<div class="chart-help"><span class="chip" data-help="macro">宏观指标是什么 ⓘ</span></div>`;
  for (const [cat, items] of byCat) {
    html += `<div class="sec-title">${MACRO_CAT_NAME[cat] || '其他'}</div><div class="macro-grid">`;
    for (const it of items) {
      const chg = macroChg(it);
      const d = chg ? chg.d : 0;
      html += `<div class="mcard" data-id="${esc(it.id)}">
        <div class="mname">${esc(it.name)}</div>
        <div class="mval">${fmtMacroNum(it.latest)}<span class="munit">${esc(it.unit)}</span></div>
        <div class="mrow"><span class="mchg ${cls(d)}">${chg ? '较上次 ' + esc(chg.txt) : '—'}</span><span class="mdate">${esc((it.series[it.series.length - 1] || [''])[0])}</span></div>
        <div class="mspark" data-id="${esc(it.id)}"></div>
      </div>`;
    }
    html += `</div>`;
  }
  document.getElementById('content').innerHTML = html;
  macroCharts.forEach(c => c.dispose());
  macroCharts = [];
  document.querySelectorAll('#content .mcard').forEach(el => {
    el.onclick = () => openMacroDetail(el.dataset.id);
  });
  document.querySelectorAll('#content .mspark').forEach(el => {
    const it = store.macro.find(x => x.id === el.dataset.id);
    if (!it) return;
    const ch = echarts.init(el);
    ch.setOption(macroSparkOpt(it));
    macroCharts.push(ch);
  });
}
async function openMacroDetail(id) {
  const it = store.macro.find(x => x.id === id) || (await api('/macro?id=' + encodeURIComponent(id)))[0];
  if (!it) return;
  store.macroSel = it;
  document.getElementById('md-name').textContent = it.name;
  const chg = macroChg(it);
  document.getElementById('md-val').innerHTML =
    `<span class="md-num">${fmtMacroNum(it.latest)}<span class="munit">${esc(it.unit)}</span></span>` +
    `<span class="mchg ${cls(chg ? chg.d : 0)}">${chg ? '较上次 ' + esc(chg.txt) : '—'}</span>`;
  document.getElementById('md-note').textContent =
    `数据源：统计局 / 央行 / 交易所 / FRED 等，序列约近 10 年。点左上角 ‹ 返回。`;
  document.getElementById('macro-detail').style.display = 'block';
  document.body.style.overflow = 'hidden';
  const box = document.getElementById('md-chart');
  // 窄屏下按视口高度自适应，先定高再 init
  const h = Math.max(280, Math.min(window.innerHeight - 200, 420));
  box.style.height = h + 'px';
  if (store.macroDetailChart) store.macroDetailChart.dispose();
  store.macroDetailChart = echarts.init(box);
  store.macroDetailChart.setOption({
    backgroundColor: 'transparent',
    grid: { left: 44, right: 12, top: 16, bottom: 28 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#222834', borderColor: '#3a4250', textStyle: { color: '#d8dce3' },
      formatter: (ps) => {
        const p = ps[0];
        return `${p.axisValue}<br/><span style="color:#4d8fd1">${esc(it.name)}</span>：${fmtMacroNum(p.value)}${esc(it.unit)}`;
      },
    },
    xAxis: {
      type: 'category', boundaryGap: false,
      data: it.series.map(p => p[0]),
      axisLine: { lineStyle: { color: '#3a4250' } },
      axisLabel: { color: '#8a8f98', fontSize: 11, hideOverlap: true },
    },
    yAxis: {
      type: 'value', scale: true,
      splitLine: { lineStyle: { color: '#262c36' } },
      axisLabel: { color: '#8a8f98', fontSize: 11 },
    },
    series: [{
      type: 'line', data: it.series.map(p => p[1]), showSymbol: false, smooth: true,
      lineStyle: { width: 2, color: '#4d8fd1' },
      areaStyle: { color: 'rgba(77,143,209,.12)' },
    }],
  });
  // 窄屏下重新计算高度
  store.macroDetailChart.resize();
}
function closeMacroDetail() {
  document.getElementById('macro-detail').style.display = 'none';
  document.body.style.overflow = '';
}

// ---------- 详情页（K线） ----------
async function showDetail(sym, markSignal = null) {
  const it = findItem(sym) || { symbol: sym, name: sym, type: 'stock' };
  store.detail = sym;
  store.markSignal = markSignal;
  if (markSignal) {
    // 自动切到信号对应的图类型，让标记落在正确的时间粒度上
    store.chartType = markSignal.rule.startsWith('daily:') ? 'daily' : 'hour';
  }
  document.getElementById('detail').style.display = 'block';
  document.getElementById('d-name').textContent = it.name;
  document.getElementById('d-sym').textContent = sym;
  updateFavBtn();
  switchChartType(store.chartType);
  loadProfile(sym);
}

function updateFavBtn() {
  const on = store.favs.has(store.detail);
  document.getElementById('d-fav').textContent = on ? '★ 已自选' : '☆ 加自选';
  document.getElementById('d-fav').style.color = on ? '#f5c242' : '#8a8f98';
}

// 日线聚合为周线/月线（OHLCV 合成，date 取该周期最后一天）
function aggregateBars(bars, period) {
  if (period === 'daily') return bars;
  const keyOf = (d) => {
    const [y, m, dd] = d.split('-').map(Number);
    if (period === 'month') return `${y}-${String(m).padStart(2, '0')}`;
    // 周：ISO 周（周一为一周开始）
    const dt = new Date(y, m - 1, dd);
    const day = (dt.getDay() + 6) % 7; // 0=周一
    const monday = new Date(dt); monday.setDate(dd - day);
    return `${monday.getFullYear()}-${String(monday.getMonth() + 1).padStart(2, '0')}-${String(monday.getDate()).padStart(2, '0')}`;
  };
  const groups = new Map();
  for (const b of bars) {
    const k = keyOf(b.date);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(b);
  }
  const out = [];
  for (const [, g] of groups) {
    out.push({
      date: g[g.length - 1].date,
      open: g[0].open,
      close: g[g.length - 1].close,
      high: Math.max(...g.map(x => x.high)),
      low: Math.min(...g.map(x => x.low)),
      volume: g.reduce((s, x) => s + x.volume, 0),
    });
  }
  return out;
}

async function loadChart(sym, limit) {
  const req = ++store.chartReq;
  store.chartRange = limit;
  document.querySelectorAll('#ranges button').forEach(b => b.classList.toggle('active', +b.dataset.limit === limit));
  let bars;
  const type = store.chartType;
  if (type === 'tick') bars = await api(`/tick?symbol=${sym}`);
  else if (type === 'hour') bars = await api(`/hour?symbol=${sym}&limit=${limit}`);
  else if (type === 'week' || type === 'month') {
    // 周/月：多拉日线再聚合（周线 limit*5 天，月线 limit*22 天）
    const days = type === 'week' ? limit * 5 : limit * 22;
    const raw = await api(`/daily?symbol=${sym}&limit=${Math.min(days, 2000)}`);
    bars = aggregateBars(raw, type);
  }
  else bars = await api(`/daily?symbol=${sym}&limit=${limit}`);
  if (req !== store.chartReq) return; // 已被更新的请求/关闭取代
  const sigs = await ensureSignalHist(sym);
  if (req !== store.chartReq) return;
  if (store.chartType === 'tick') renderTick(bars);
  else renderCandle(bars, sigs);
  // 最新行情头部
  const q = snap(sym);
  document.getElementById('d-quote').innerHTML = q ? quoteGridHtml(q) : '';
}

// 小时线时间戳 202608311400 -> 08-31 14:00；日线原样（YYYY-MM-DD）
const fmtX = (t) => store.chartType === 'hour'
  ? `${t.slice(4, 6)}-${t.slice(6, 8)} ${t.slice(8, 10)}:${t.slice(10, 12)}`
  : t;

// 信号定位：日线信号 -> YYYY-MM-DD；盘中信号 -> 对应那根60分钟K线的时间戳
// 腾讯小时线的时间戳是该小时的结束时刻：09:30-10:29→1030, 10:30-11:29→1130, 13:00-13:59→1400, 14:00-15:00→1500
function sigKey(s) {
  const d = new Date(s.ts * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  const ymd = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  if (s.rule.startsWith('daily:')) return ymd;
  const hm = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}`;
  const hh = d.getHours(), mm = d.getMinutes();
  let end;
  if (hh < 10 || (hh === 10 && mm < 30)) end = '1030';
  else if (hh < 11 || (hh === 11 && mm < 30)) end = '1130';
  else if (hh < 14) end = '1400';
  else end = '1500';
  return hm + end;
}
// 键按当前图类型对齐：日线/周/月模式只要日期，小时模式要完整时间戳
const alignKey = (k) => (store.chartType === 'hour' ? k : (k.length > 10 ? `${k.slice(0, 4)}-${k.slice(4, 6)}-${k.slice(6, 8)}` : k));

async function ensureSignalHist(sym) {
  if (store.signalHist && store.signalHist.sym === sym) return store.signalHist.list;
  try {
    const list = await api(`/signals?symbol=${sym}&limit=500`);
    store.signalHist = { sym, list };
  } catch { store.signalHist = { sym, list: [] }; }
  return store.signalHist.list;
}

const RANGES = {
  daily: { limits: [60, 120, 250, 500, 1000], labels: ['3月', '半年', '1年', '2年', '4年'] },
  hour: { limits: [20, 40, 80, 160, 320], labels: ['1周', '2周', '1月', '2月', '4月'] },
  week: { limits: [26, 52, 104, 208, 400], labels: ['半年', '1年', '2年', '4年', '8年'] },
  month: { limits: [12, 24, 48, 96, 144], labels: ['1年', '2年', '4年', '8年', '12年'] },
};

function switchChartType(type) {
  store.chartType = type;
  document.querySelectorAll('#ctype button').forEach(b => b.classList.toggle('active', b.dataset.type === type));
  const isTick = type === 'tick';
  document.getElementById('ranges').style.display = isTick ? 'none' : 'flex';
  const chips = document.querySelector('#detail .chart-help');
  if (chips) chips.style.display = isTick ? 'none' : 'flex';
  updateSignalToggle();
  if (!isTick) {
    const r = RANGES[type];
    const btns = document.querySelectorAll('#ranges button');
    btns.forEach((b, i) => { b.dataset.limit = r.limits[i]; b.textContent = r.labels[i]; });
    store.chartRange = r.limits.includes(store.chartRange) ? store.chartRange : r.limits[2];
    btns.forEach(b => b.classList.toggle('active', +b.dataset.limit === store.chartRange));
  }
  loadChart(store.detail, store.chartRange);
}

function updateSignalToggle() {
  const wrap = document.getElementById('sig-toggle-wrap');
  if (!wrap) return;
  wrap.style.display = store.chartType === 'tick' ? 'none' : 'inline-flex';
  const btn = document.getElementById('sig-toggle');
  btn.textContent = store.showAllSignals ? '历史信号：开' : '历史信号';
  btn.classList.toggle('on', store.showAllSignals);
}

function renderCandle(bars, sigs = []) {
  const el = document.getElementById('chart');
  el.innerHTML = '';
  if (!bars.length) { el.innerHTML = '<div class="empty">暂无数据</div>'; return; }
  if (typeof echarts === 'undefined') { el.innerHTML = '<div class="empty">图表库加载失败</div>'; return; }
  if (store.chart) { store.chart.dispose(); }
  store.chart = echarts.init(el);
  const dates = bars.map(b => b.date);
  const kdata = bars.map(b => [b.open, b.close, b.low, b.high]);
  const vols = bars.map(b => b.volume);
  const ma = (n) => bars.map((_, i) => i < n - 1 ? null : +(bars.slice(i - n + 1, i + 1).reduce((s, x) => s + x.close, 0) / n).toFixed(3));
  const mas = [['MA5', ma(5)], ['MA20', ma(20)], ['MA60', ma(60)]]
    .filter(([, v]) => v.some(x => x != null)); // 数据不足的均线不画（如 60 根时 MA60）

  // 信号标记：把信号定位到对应那根K线（y 用该根K线最高价，点落在 K 线上方）
  const isHour = store.chartType === 'hour';
  const dateIdx = new Map(dates.map((d, i) => [d, i]));
  // 周/月模式：把信号日期映射到所属周期（该周期最后一根K线的日期）
  const periodOf = (day) => {
    for (let i = dates.length - 1; i >= 0; i--) {
      if (dates[i] <= day) return dates[i];
    }
    return null;
  };
  const marks = [];
  const seen = new Set();
  for (const s of sigs) {
    const isDailyRule = s.rule.startsWith('daily:');
    // 小时模式只画盘中信号；日/周/月模式只画日线信号
    if (isHour ? isDailyRule : !isDailyRule) continue;
    if (!store.showAllSignals && (!store.markSignal || s.id !== store.markSignal.id)) continue;
    let key = alignKey(sigKey(s));
    if (store.chartType === 'week' || store.chartType === 'month') {
      key = periodOf(key);
      if (!key) continue;
    }
    if (seen.has(key)) continue;
    seen.add(key);
    if (dateIdx.has(key)) {
      const i = dateIdx.get(key);
      marks.push({
        name: s.message,
        value: [key, bars[i].high],
      });
    }
  }
  // y 轴顶部留 12% 空间，避免信号标记与K线重叠
  const hi = Math.max(...bars.map(b => b.high), ...marks.map(m => m.value[1]));
  const lo = Math.min(...bars.map(b => b.low));
  const yTop = hi + (hi - lo) * 0.12;

  store.chart.setOption({
    backgroundColor: 'transparent',
    animation: false,
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#222834', borderColor: '#262c36',
      textStyle: { color: '#d8dce3', fontSize: 12 },
      axisPointer: { type: 'cross', label: { backgroundColor: '#4d8fd1' } },
    },
    legend: { data: mas.map(([n]) => n), textStyle: { color: '#8a8f98' }, top: 0 },
    grid: [
      { left: 50, right: 10, top: 30, height: '55%' },
      { left: 50, right: 10, top: '72%', height: '18%' },
    ],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, axisLabel: { color: '#8a8f98', formatter: fmtX } },
      { type: 'category', data: dates, gridIndex: 1, axisLabel: { show: false } },
    ],
    yAxis: [
      { scale: true, max: yTop, gridIndex: 0, splitLine: { lineStyle: { color: '#262c36' } }, axisLabel: { color: '#8a8f98' } },
      { scale: true, gridIndex: 1, splitLine: { show: false }, axisLabel: { show: false } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1] },
      { type: 'slider', xAxisIndex: [0, 1], bottom: 4, height: 18, borderColor: '#262c36', textStyle: { color: '#8a8f98' } },
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', data: kdata, xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: { color: '#e05555', color0: '#3fbf7f', borderColor: '#e05555', borderColor0: '#3fbf7f' },
      },
      ...mas.map(([n, v]) => ({ name: n, type: 'line', data: v, smooth: true, showSymbol: false, lineStyle: { width: 1, color: { MA5: '#f5c242', MA20: '#4d8fd1', MA60: '#b06fd1' }[n] } })),
      {
        name: '信号', type: 'scatter', xAxisIndex: 0, yAxisIndex: 0,
        symbol: 'triangle', symbolSize: 14, data: marks,
        itemStyle: { color: '#e05555', borderColor: '#fff', borderWidth: 1 },
        label: {
          show: true, position: 'top', fontSize: 10, color: '#fff',
          backgroundColor: '#e05555', borderRadius: 3, padding: [2, 5],
          formatter: '信号',
        },
        tooltip: { formatter: (p) => p.name },
        z: 10,
      },
      {
        name: '成交量', type: 'bar', data: vols, xAxisIndex: 1, yAxisIndex: 1,
        itemStyle: {
          // 与 K 线红绿一致：收>=开为红(涨)，否则绿(跌)
          color: (p) => {
            const b = bars[p.dataIndex];
            return b && b.close >= b.open ? '#e05555' : '#3fbf7f';
          },
        },
      },
    ],
  });
  // 点信号标记：弹出信号详情浮层
  store.chart.off('click');
  store.chart.on('click', (p) => {
    if (p.seriesName === '信号' && p.name) showSigTip(p.name, p.event.event);
  });
}

// 信号详情浮层（点红色三角弹出）
function showSigTip(text, evt) {
  let tip = document.getElementById('sig-tip');
  if (!tip) {
    tip = document.createElement('div');
    tip.id = 'sig-tip';
    document.getElementById('detail').appendChild(tip);
  }
  tip.innerHTML = `<div class="sig-tip-msg">${esc(text)}</div><span class="sig-tip-x">✕</span>`;
  tip.style.display = 'block';
  tip.querySelector('.sig-tip-x').onclick = () => { tip.style.display = 'none'; };
}

function renderTick(bars) {
  const el = document.getElementById('chart');
  el.innerHTML = '';
  if (!bars.length) { el.innerHTML = '<div class="empty">暂无分时数据</div>'; return; }
  if (typeof echarts === 'undefined') { el.innerHTML = '<div class="empty">图表库加载失败</div>'; return; }
  if (store.chart) { store.chart.dispose(); }
  store.chart = echarts.init(el);
  const times = bars.map(b => b.date.slice(8, 10) + ':' + b.date.slice(10, 12));
  const prices = bars.map(b => b.price);
  const q = snap(store.detail);
  const prevClose = q ? q.prev_close : null;
  const avgPrice = q && q.avg_price > 0 ? q.avg_price : null;
  const markLines = [];
  if (prevClose) markLines.push({
    yAxis: prevClose,
    label: { show: true, formatter: '昨收', color: '#8a8f98' },
    lineStyle: { color: '#8a8f98', type: 'dashed' },
  });
  if (avgPrice) markLines.push({
    yAxis: avgPrice,
    label: { show: true, formatter: '均价', color: '#f5c242' },
    lineStyle: { color: '#f5c242', type: 'dashed' },
  });
  store.chart.setOption({
    backgroundColor: 'transparent',
    animation: false,
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 12, top: 20, bottom: 24 },
    xAxis: { type: 'category', data: times, axisLabel: { color: '#8a8f98' } },
    yAxis: { scale: true, splitLine: { lineStyle: { color: '#262c36' } }, axisLabel: { color: '#8a8f98' } },
    series: [{
      name: '价格', type: 'line', data: prices, smooth: true, showSymbol: false,
      lineStyle: { width: 2, color: '#4d8fd1' },
      areaStyle: { color: 'rgba(77,143,209,.12)' },
      markLine: markLines.length ? {
        silent: true, symbol: 'none',
        data: markLines,
      } : undefined,
    }],
  });
}

async function loadProfile(sym) {
  const req = ++store.profileReq;
  let rows;
  if (store.profiles[sym]) rows = store.profiles[sym];
  else rows = await api(`/profile?symbol=${sym}`);
  if (req !== store.profileReq) return;
  store.profiles[sym] = rows;
  const el = document.getElementById('profile-chart');
  el.innerHTML = '';
  if (!rows.length) { el.innerHTML = '<div class="empty">暂无量能数据</div>'; return; }
  if (typeof echarts === 'undefined') { el.innerHTML = '<div class="empty">图表库加载失败</div>'; return; }
  if (store.profileChart) { store.profileChart.dispose(); }
  store.profileChart = echarts.init(el);
  store.profileChart.setOption({
    backgroundColor: 'transparent',
    animation: false,
    tooltip: { trigger: 'axis' },
    grid: { left: 52, right: 10, top: 10, bottom: 22 },
    xAxis: { type: 'category', data: rows.map(r => r.hm), axisLabel: { color: '#8a8f98', interval: 5 } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#262c36' } }, axisLabel: { color: '#8a8f98', formatter: (v) => v >= 1e4 ? (v / 1e4).toFixed(0) + '万' : v } },
    series: [{ type: 'bar', data: rows.map(r => r.avg_vol), itemStyle: { color: '#4d8fd1' }, barMaxWidth: 10 }],
  });
}

function hideDetail() {
  store.chartReq++; // 使在途的 loadChart 结果作废
  store.profileReq++;
  store.markSignal = null;
  store.showAllSignals = false;
  document.getElementById('detail').style.display = 'none';
  const tip = document.getElementById('sig-tip');
  if (tip) tip.style.display = 'none';
  if (store.chart) { store.chart.dispose(); store.chart = null; }
  if (store.profileChart) { store.profileChart.dispose(); store.profileChart = null; }
}

// ---------- 导航 ----------
const pages = { watch: renderWatch, index: renderIndex, macro: renderMacro, signals: renderSignals, me: renderMe };
function switchPage(p) {
  store.page = p;
  hideDetail();
  if (p !== 'macro') {
    macroCharts.forEach(c => c.dispose());
    macroCharts = [];
  }
  document.querySelectorAll('#nav span').forEach(el => el.classList.toggle('active', el.dataset.page === p));
  pages[p]();
}

async function boot() {
  store.dir = await api('/stocks');
  await refreshSnapshots();
  switchPage('watch');
  setInterval(async () => {
    await refreshSnapshots().catch(() => {});
    if (document.getElementById('detail').style.display === 'block') {
      const q = snap(store.detail); if (q) loadChartHeaderOnly();
    } else if (store.page === 'watch' || store.page === 'index') {
      pages[store.page]();
    }
  }, 60000);
}
function loadChartHeaderOnly() {
  const q = snap(store.detail);
  if (q && store.detail) showDetailQuoteOnly();
}
function showDetailQuoteOnly() {
  const q = snap(store.detail);
  document.getElementById('d-quote').innerHTML = q ? quoteGridHtml(q) : '';
}

document.querySelectorAll('#nav span').forEach(el => el.onclick = () => switchPage(el.dataset.page));
document.getElementById('d-close').onclick = hideDetail;
document.getElementById('md-close').onclick = closeMacroDetail;
document.getElementById('macro-detail').onclick = (e) => { if (e.target.id === 'macro-detail') closeMacroDetail(); };
document.getElementById('d-fav').onclick = () => {
  if (!store.detail) return;
  store.favs.has(store.detail) ? store.favs.delete(store.detail) : store.favs.add(store.detail);
  saveFavs(); updateFavBtn();
};
document.querySelectorAll('#ranges button').forEach(b => b.onclick = () => loadChart(store.detail, +b.dataset.limit));
document.querySelectorAll('#ctype button').forEach(b => b.onclick = () => switchChartType(b.dataset.type));
document.getElementById('sig-toggle').onclick = () => {
  store.showAllSignals = !store.showAllSignals;
  updateSignalToggle();
  loadChart(store.detail, store.chartRange);
};

boot().catch(e => {
  document.getElementById('content').innerHTML = `<div class="empty">加载失败: ${e.message}</div>`;
});

// ---------- 新手帮助（ⓘ → 底部解释面板 + 可复制的 AI 提问提示词） ----------
const HELP = {
  open: {
    title: '今开',
    what: '今天早上 9:30 开盘时，第一笔成交的价格。',
    when: '想确认今天开盘是高开还是低开（比昨收高就是高开，比昨收低就是低开），就看它。',
    prompt: '用大白话给我讲讲股票的"今开"是什么意思？什么是高开、低开？',
  },
  prev_close: {
    title: '昨收',
    what: '上一个交易日收盘时的价格，也就是今天涨跌的"起跑线"——今天的涨跌幅，都是拿它当基准算出来的。',
    when: '想知道今天到底涨了还是跌了、涨了多少，先看昨收。',
    prompt: '请用大白话解释股票的"昨收价"是什么意思，为什么涨跌幅要拿昨收当基准？',
  },
  high: {
    title: '最高',
    what: '今天一整天里，价格最高到过多少。',
    when: '想知道这只股票今天冲高到什么位置、有没有到过高位，就看它。',
    prompt: '股票行情里的"最高价"是什么意思？怎么用它辅助判断卖点？',
  },
  low: {
    title: '最低',
    what: '今天一整天里，价格最低到过多少。',
    when: '想知道今天跌到什么位置、哪里有人愿意接盘，就看它。',
    prompt: '股票行情里的"最低价"是什么意思？怎么用它辅助判断支撑位？',
  },
  volume: {
    title: '成交量',
    what: '今天一共成交了多少手（1手 = 100股）。买卖越热闹，这个数字越大。',
    when: '价格在涨、成交量也在放大，说明这个涨比较实在；如果涨的时候没人交易（缩量），就要多留个心眼。',
    prompt: '用大白话讲讲股票的"成交量"是什么意思？"放量"和"缩量"分别说明什么？',
  },
  amount: {
    title: '成交额',
    what: '今天所有买卖加起来，一共花了多少钱（这里以万元为单位）。',
    when: '想知道这只股票有多受资金关注、规模大不大，就看它。',
    prompt: '股票的"成交额"是什么意思？它和"成交量"有什么区别？',
  },
  turnover: {
    title: '换手率',
    what: '今天换手买卖的股票，占流通股总数的比例。换手率越高，说明买卖越频繁、越热闹。',
    when: '换手率突然变得很高，往往说明多空分歧大，或者有大资金在进出。',
    prompt: '用大白话解释"换手率"是什么？换手率高和低分别代表什么？',
  },
  time: {
    title: '更新时间',
    what: '这一行行情数据是几点更新的。',
    when: '先看它再下结论：如果还是早上的时间，说明数据还没刷新，别拿旧数据做决定。',
    prompt: '行情页面上的"更新时间"是什么意思？为什么看行情前要先看更新时间？',
  },
  pct: {
    title: '涨跌幅',
    what: '现在的价格跟昨天收盘价相比，涨了或跌了百分之几。红色带 + 号是涨，绿色带 - 号是跌。',
    when: '想快速知道这只股票今天表现怎么样，第一眼看它就行。',
    prompt: '股票里的"涨跌幅"是怎么算出来的？红色和绿色分别代表什么？',
  },
  kline: {
    title: 'K线',
    what: '把每天的开盘价、收盘价、最高价、最低价，画成一根柱子。红色柱子 = 今天涨了，绿色柱子 = 今天跌了；柱子上下那两根细线，是当天到过的最高价和最低价。',
    when: '看一只股票一天、或一段时间里的价格走势，就靠它。',
    prompt: '我是新手，请用最简单的话讲讲K线图怎么看？一根红柱子和一根绿柱子分别代表什么？',
  },
  redgreen: {
    title: '红涨绿跌',
    what: '中国股市的规矩：红色代表上涨，绿色代表下跌，跟国外正好相反。所以这里的红不是"危险"，是"涨了"。',
    when: '看任何行情颜色之前，先记住这一条，就不会把红当坏消息了。',
    prompt: '为什么中国股市是红涨绿跌、跟国外相反？用大白话给我解释清楚。',
  },
  ma5: {
    title: 'MA5 均线',
    what: '把最近 5 天的收盘价算平均，再把每天的这个平均价连成一条线。因为只算 5 天，它对价格变化反应最快，代表短期走势。',
    when: '想看最近几天的短期走势，就看它；价格在 MA5 上方，说明短期偏强。',
    prompt: '用大白话讲讲 MA5 均线是什么？它代表什么时间段，有什么用处？',
  },
  ma20: {
    title: 'MA20 均线',
    what: '把最近 20 天（约一个月）的收盘价算平均，连成一条线，代表一个月左右的走势，很多人把它叫作"生命线"。',
    when: '想判断中期趋势是向上还是向下，看它最常用。',
    prompt: '用大白话解释 MA20 均线（20日均线）是什么？为什么很多人把它叫"生命线"？',
  },
  ma60: {
    title: 'MA60 均线',
    what: '把最近 60 天（约一个季度）的收盘价算平均，连成一条线，代表更长时间的大方向。',
    when: '想判断大方向、这只股票整体是走强还是走弱，就看它；价格站上 MA60，通常说明中期走强。',
    prompt: '用大白话解释 MA60 均线是什么意思？价格在 MA60 上方和下方分别说明什么？',
  },
  volbar: {
    title: '成交量柱',
    what: 'K线图下方那一根根竖条，每天一根，竖条越高代表当天成交量越大，颜色跟当天涨跌一致（红涨绿跌）。',
    when: '配合 K 线一起看：涨的时候竖条在长高，说明这个涨更可信。',
    prompt: 'K线图下面那些竖条是什么？怎么配合上面的K线一起看？',
  },
  hour: {
    title: '小时线',
    what: '把一天 4 小时交易分成 4 根 60 分钟的柱子，每根代表一小时内的开盘、收盘、最高、最低。比日线更细，能看出一天里价格是怎么走的。注意：小时线数据目前只保留最近约 4 个月。',
    when: '想复盘某天盘中怎么波动、看一天内哪个时段走强走弱，就切到小时线。',
    prompt: '用大白话讲讲股票的"小时线"（60分钟K线）和日线有什么区别？什么时候适合看小时线？',
  },
  tick: {
    title: '今日分时',
    what: '把今天从开盘到现在的价格，按时间连成一条线。横轴是时间，纵轴是价格；那条灰色虚线是昨收价——线在虚线上方，说明今天暂时是涨的。',
    when: '想知道今天盘中涨跌的整个过程、现在处在全天哪个位置，看分时最直观。',
    prompt: '用大白话解释股票"分时图"怎么从零看起？上面那条灰色虚线（昨收）代表什么？',
  },
  profile: {
    title: '日内量能分布',
    what: '把过去 10 天每天的成交量，按 5 分钟一个时段拆开，算出每个时段（比如 09:35、10:00）的平均成交量，画成一根根柱子。柱子越高，说明这个时段平时买卖越活跃。',
    when: '配合"放量"判断：现在这个时段的实际成交量明显高于对应柱子，说明此刻比平时活跃；明显低于，说明清淡。',
    prompt: '用大白话解释"同时段量能分布"是什么意思？怎么用它判断股票放量、缩量？',
  },
  total_mv: {
    title: '总市值',
    what: '这家公司所有的股票加起来，按现在的价格算，一共值多少钱。数值越大，公司块头越大。',
    when: '想快速知道这家公司是大公司还是小公司，就看它。千亿以上一般算大盘股。',
    prompt: '用大白话解释"总市值"是什么意思？大盘股和小盘股有什么区别？',
  },
  circ_mv: {
    title: '流通市值',
    what: '这家公司里，可以在市场上自由买卖的那部分股票，按现在的价格算值多少钱。有些股票被大股东锁定不能卖，所以流通市值通常比总市值小一点。',
    when: '想知道真正在市场上交易、能影响价格的盘子有多大，看它。',
    prompt: '用大白话解释"流通市值"和"总市值"有什么区别？',
  },
  pe_ttm: {
    title: '市盈率（PE）',
    what: '现在的股价，相当于公司一年盈利的多少倍。比如 PE=11，意思是按现在的盈利水平，大约 11 年能赚回买股的钱。一般来说，PE 低说明相对便宜，PE 高说明相对贵（但也要看行业）。',
    when: '想粗略判断这只股票"贵不贵"，就看它。和同行业公司比、和它自己的历史比更有意义。',
    prompt: '用大白话解释"市盈率（PE）"是什么意思？PE高好还是低好？怎么看一只股票贵不贵？',
  },
  week: {
    title: '周线',
    what: '把一周的 5 根日线合成一根柱子：周一的开盘价、周五的收盘价、这一周的最高最低价。比日线看得更远，能过滤掉单日的小波动。',
    when: '想看几个月到几年的中期趋势，不被每天的涨跌干扰，就切到周线。',
    prompt: '用大白话解释股票的"周线"和日线有什么区别？什么时候适合看周线？',
  },
  month: {
    title: '月线',
    what: '把一个月的日线合成一根柱子。一根柱子代表一个月的开、收、高、低。是看长期大趋势用的。',
    when: '想判断这只股票几年来的大方向是向上还是向下，就切到月线。',
    prompt: '用大白话解释股票的"月线"是什么？怎么用月线判断长期趋势？',
  },
  avgline: {
    title: '均价线',
    what: '今天所有成交的平均价格。把今天每笔买卖的钱加起来除以总股数得到。它代表"今天大家平均的买入成本"。',
    when: '分时图里，价格在均价线上方，说明今天买的人总体是赚的（强势）；在下方，说明总体是亏的（弱势）。',
    prompt: '用大白话解释分时图里的"均价线"是什么意思？价格在均价线上方和下方分别说明什么？',
  },
  watch: {
    title: '自选',
    what: '你自己收藏的股票列表。在详情页点"加自选"，就能把常看的股票收进来，下次打开直接看。',
    when: '把每天都要盯的股票加进自选，就不用每次重新找。',
    prompt: '股票软件里的"自选股"是什么意思？一般怎么用？',
  },
  index: {
    title: '指数',
    what: '指数不是一只股票，而是一篮子股票的综合表现。比如上证指数代表整个沪市大盘，沪深300代表规模最大的 300 家公司。',
    when: '想快速知道整个市场今天好不好、大环境怎么样，就看指数。',
    prompt: '用大白话解释什么是股票"指数"？宽基指数、规模指数、基准指数都是什么意思？',
  },
  signal: {
    title: '信号',
    what: '系统按照设定好的规则，自动盯盘算出来的提醒。比如价格突破了某个位置、成交量突然放大等，发现值得留意的动静就会记录下来。',
    when: '每天花一分钟扫一眼信号页，可以帮你发现平时没注意到的机会或风险。',
    prompt: '股票里的"交易信号"一般指什么？常见的金叉、死叉、放量是什么意思？',
  },
  me: {
    title: '我的',
    what: '你自己的专区：这里能看到你收藏的股票，以及数据同步的状态（每天几点更新）。',
    when: '想整理自选、确认数据是否正常更新，就到这里来。',
    prompt: '帮我看看我的自选和同步状态页面应该怎么用？',
  },
  macro: {
    title: '宏观指标',
    what: '这些是影响整个市场的"大环境"数据，不是某一只股票。比如：货币供应（M1/M2）代表市场里有多少钱，CPI/PPI 代表物价贵不贵，PMI 代表工厂生意好不好，两融余额代表大家借钱炒股的热度。卡片上的小曲线是最近 10 年的走势，红色数字是比上一次数据涨了，绿色是跌了。',
    when: '想知道现在整个市场处在一个什么样的环境（钱多不多、经济热不热、海外紧不紧），就来看这个页面。大环境会间接影响所有股票。',
    prompt: '用大白话给我讲讲解读宏观指标的基本方法：M1/M2、CPI、PPI、PMI、社融、两融余额这些分别代表什么？怎么看它们现在高不高、对股市有什么影响？',
  },
  newbie: {
    title: '新手入门：一张K线图怎么看',
    what: '一共四步：① 记住"红涨绿跌"，红 = 涨、绿 = 跌；② 图里每根柱子代表一天，柱子的高低是当天的价格范围；③ 图里那几条彩色细线是"均线"（MA5、MA20、MA60），代表最近 5 天、20 天、60 天的平均价格走势，时间越长的线方向越稳；④ 图下方一根根竖条是每天的成交量，条越高当天买卖越热闹。',
    when: '第一次打开这个页面，或者想跟家人解释这张图，就先看这一条。',
    prompt: '我是完全不懂股票的新手，请用最通俗的话，一步步教我怎么看懂一张K线图，包括红绿颜色、柱子、彩色均线和下面的成交量。',
  },
};

function openHelp(key) {
  const h = HELP[key];
  if (!h) return;
  document.getElementById('help-title').textContent = h.title;
  document.getElementById('help-what').textContent = h.what;
  document.getElementById('help-when').textContent = h.when;
  document.getElementById('help-prompt').textContent = h.prompt;
  document.getElementById('help-copy').textContent = '复制提示词';
  document.getElementById('help').classList.add('show');
  document.body.style.overflow = 'hidden'; // 打开面板时锁住背景滚动
}

function closeHelp() {
  document.getElementById('help').classList.remove('show');
  document.body.style.overflow = '';
}

function fallbackCopy(text, done) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); done(); } catch (e) { /* 忽略 */ }
  document.body.removeChild(ta);
}

function copyPrompt() {
  const t = document.getElementById('help-prompt').textContent;
  const done = () => {
    const b = document.getElementById('help-copy');
    b.textContent = '已复制 ✓';
    setTimeout(() => { b.textContent = '复制提示词'; }, 1500);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(t).then(done).catch(() => fallbackCopy(t, done));
  } else {
    fallbackCopy(t, done);
  }
}

// 捕获阶段拦截：点 ⓘ / 术语打开解释，且不触发卡片、页签的点击
document.addEventListener('click', (e) => {
  const t = e.target.closest('[data-help]');
  if (t) { e.stopPropagation(); openHelp(t.dataset.help); return; }
  if (e.target.closest('#help-mask') || e.target.closest('#help-close')) closeHelp();
}, true);

document.getElementById('help-copy').onclick = copyPrompt;
document.getElementById('help-fab').onclick = () => openHelp('newbie');
