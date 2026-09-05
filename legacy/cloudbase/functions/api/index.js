/**
 * api：只读行情/数据查询接口（P0）。
 *
 * 路由（均支持 CORS，供后续手机端网页直连）：
 *   GET /api/health                 健康检查
 *   GET /api/quote?symbols=601169,sh000300   最新快照（缺省返回全部）
 *   GET /api/daily?symbol=601169&limit=250  历史日线（升序，最新在后）
 *   GET /api/stocks?type=stock|index        股池/指数目录（缺省返回全部）
 *   GET /api/signals?limit=50&symbol=      最近信号（symbol 过滤）
 *   GET /api/stats                         各集合文档数
 *   GET /api/hour?symbol=&limit=           60分钟线（升序，最新在后）
 *   GET /api/tick?symbol=&date=            当日分时帧（date 形如 20260901，缺省今天）
 *   GET /api/profile?symbol=               日内量能分布（同时段5分钟均量）
 */
const cloudbase = require('@cloudbase/node-sdk')

const app = cloudbase.init({ env: cloudbase.SYMBOL_CURRENT_ENV })
const db = app.database()
const _ = db.command

// 简易 TTL 缓存：云函数实例内按路由+参数缓存查询结果。
// 数据更新节奏远慢于页面轮询（快照 5 分钟、日线/宏观日更），
// 多页面/多标签的轮询共享一份结果，可把数据库读取量降到原 1/N。
const CACHE = new Map()
const CACHE_TTL = {
  '/quote': 30 * 1000,    // 快照每 5 分钟才更新，30s 缓存不影响体验
  '/tick': 30 * 1000,     // 分时帧 5 分钟一帧
  '/daily': 300 * 1000,   // 日线收盘后更新
  '/hour': 300 * 1000,
  '/signals': 15 * 1000,  // 信号较实时，但秒级新鲜度无意义
  '/stocks': 300 * 1000,  // 目录几乎不变
  '/profile': 300 * 1000,
  '/macro': 300 * 1000,   // 日更
  '/stats': 300 * 1000,
}
function cacheKey(p, query) {
  const q = Object.keys(query).filter((k) => query[k] !== undefined && query[k] !== '')
    .sort().map((k) => `${k}=${query[k]}`).join('&')
  return p + (q ? '?' + q : '')
}
async function cached(p, query, fn) {
  const key = cacheKey(p, query)
  const hit = CACHE.get(key)
  const ttl = CACHE_TTL[p] || 0
  if (hit && Date.now() - hit.ts < ttl) return hit.data
  const data = await fn()
  if (data && data.statusCode) return data  // 参数错误等非业务响应：不缓存，原样返回
  CACHE.set(key, { ts: Date.now(), data })
  if (CACHE.size > 200) {  // 防内存无限增长
    for (const [k, v] of CACHE) if (Date.now() - v.ts > 600 * 1000) CACHE.delete(k)
  }
  return data
}
// 业务响应用 json 包装；错误响应（带 statusCode）原样透出
async function route(p, query, fn) {
  const out = await cached(p, query, fn)
  return out && out.statusCode ? out : json(out)
}

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type,x-sync-token',
}

const json = (data) => ({ statusCode: 200, headers: CORS, body: JSON.stringify(data) })
const err = (code, msg) => ({ statusCode: code, headers: CORS, body: JSON.stringify({ error: msg }) })

async function quote(query) {
  const symbols = String(query.symbols || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
  if (symbols.length === 0) {
    const res = await db.collection('snapshots').limit(200).get()
    return { data: res.data, ts: Date.now() }
  }
  const res = await db.collection('snapshots').where({ symbol: _.in(symbols) }).limit(200).get()
  return { data: res.data, ts: Date.now() }
}

async function daily(query) {
  const symbol = String(query.symbol || '').trim()
  if (!symbol) return err(400, 'symbol required')
  const limit = Math.min(Math.max(parseInt(query.limit, 10) || 120, 1), 2000)
  const res = await db.collection('daily_bars')
    .where({ symbol })
    .orderBy('date', 'desc')
    .limit(limit)
    .get()
  return { data: res.data.reverse(), ts: Date.now() }
}

async function stocks(query) {
  const type = String(query.type || '').trim()
  const cond = ['stock', 'index', 'industry'].includes(type) ? { type } : {}
  const res = await db.collection('stocks').where(cond).limit(500).get()
  return { data: res.data, ts: Date.now() }
}

async function signals(query) {
  const limit = Math.min(Math.max(parseInt(query.limit, 10) || 50, 1), 500)
  const symbol = String(query.symbol || '').trim()
  let q = db.collection('signals')
  if (symbol) q = q.where({ symbol })
  const res = await q.orderBy('ts', 'desc').limit(limit).get()
  return { data: res.data, ts: Date.now() }
}

async function stats() {
  const names = ['daily_bars', 'hour_bars', 'ticks', 'stocks', 'snapshots', 'signals', 'vol_profile', 'macro_indicators']
  const out = {}
  await Promise.all(names.map(async (n) => {
    try {
      const r = await db.collection(n).count()
      out[n] = r.total || 0
    } catch (e) {
      out[n] = -1
    }
  }))
  return { data: out, ts: Date.now() }
}

async function hour(query) {
  const symbol = String(query.symbol || '').trim()
  if (!symbol) return err(400, 'symbol required')
  const limit = Math.min(Math.max(parseInt(query.limit, 10) || 120, 1), 2000)
  const res = await db.collection('hour_bars')
    .where({ symbol })
    .orderBy('date', 'desc')
    .limit(limit)
    .get()
  return { data: res.data.reverse(), ts: Date.now() }
}

async function tick(query) {
  const symbol = String(query.symbol || '').trim()
  if (!symbol) return err(400, 'symbol required')
  const day = String(query.date || '').trim() || new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10).replace(/-/g, '')
  let res = await db.collection('ticks')
    .where({ symbol, date: _.gte(day + '0000').and(_.lte(day + '2359')) })
    .orderBy('date', 'asc')
    .limit(400)
    .get()
  let rows = res.data
  let usedDay = day
  if (!rows.length) {
    // 当天无帧（如周末/盘前）时回退到最近一个有分时帧的交易日
    const latest = await db.collection('ticks')
      .where({ symbol })
      .orderBy('date', 'desc')
      .limit(400)
      .get()
    if (latest.data.length) {
      const max = latest.data[0].date.slice(0, 8)
      rows = latest.data.filter((x) => x.date.slice(0, 8) === max).reverse()
      usedDay = max
    }
  }
  return { data: rows, day: usedDay, ts: Date.now() }
}

async function profile(query) {
  const symbol = String(query.symbol || '').trim()
  if (!symbol) return err(400, 'symbol required')
  const res = await db.collection('vol_profile')
    .where({ symbol })
    .orderBy('hm', 'asc')
    .limit(100)
    .get()
  return { data: res.data, ts: Date.now() }
}

async function macro(query) {
  const id = String(query.id || '').trim()
  let q = db.collection('macro_indicators')
  if (id) q = q.where({ id })
  const res = await q.limit(100).get()
  let rows = res.data
  if (!id) {
    // 列表视图只回尾部 ~140 点控制响应体（HTTP 访问服务 ~100KB 上限）；详情用 ?id= 取全量
    rows = rows.map((x) => ({ ...x, series: (x.series || []).slice(-140) }))
  }
  return { data: rows, ts: Date.now() }
}

exports.main = async (event) => {
  const method = (event.httpMethod || 'GET').toUpperCase()
  if (method === 'OPTIONS') {
    return { statusCode: 204, headers: CORS, body: '' }
  }
  const query = event.queryStringParameters || {}
  const p = (event.path || '').replace(/\/+$/, '')

  // HTTP 访问服务可能透传完整路径(/api/health)或挂载前缀之后的路径(/health)，按后缀匹配
  const routes = ['/health', '/quote', '/daily', '/stocks', '/signals', '/stats', '/hour', '/tick', '/profile', '/macro']
  const hit = routes.find((r) => p.endsWith(r))
  switch (hit) {
    case '/health':
      return json({ ok: true, ts: Date.now() })
    case '/quote':
      return json(await cached('/quote', query, () => quote(query)))
    case '/daily':
      return route('/daily', query, () => daily(query))
    case '/stocks':
      return json(await cached('/stocks', query, () => stocks(query)))
    case '/signals':
      return json(await cached('/signals', query, () => signals(query)))
    case '/stats':
      return json(await cached('/stats', query, () => stats()))
    case '/hour':
      return route('/hour', query, () => hour(query))
    case '/tick':
      return json(await cached('/tick', query, () => tick(query)))
    case '/profile':
      return json(await cached('/profile', query, () => profile(query)))
    case '/macro':
      return json(await cached('/macro', query, () => macro(query)))
    default:
      return err(404, `not found (path=${p})`)
  }
}
