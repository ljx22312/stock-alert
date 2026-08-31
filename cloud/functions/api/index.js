/**
 * api：只读行情/数据查询接口（P0）。
 *
 * 路由（均支持 CORS，供后续手机端网页直连）：
 *   GET /api/health                 健康检查
 *   GET /api/quote?symbols=601169,sh000300   最新快照（缺省返回全部）
 *   GET /api/daily?symbol=601169&limit=250  历史日线（升序，最新在后）
 *   GET /api/stocks?type=stock|index        股池/指数目录（缺省返回全部）
 *   GET /api/signals?limit=50               最近信号
 */
const cloudbase = require('@cloudbase/node-sdk')

const app = cloudbase.init({ env: cloudbase.SYMBOL_CURRENT_ENV })
const db = app.database()
const _ = db.command

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
  const cond = type === 'stock' || type === 'index' ? { type } : {}
  const res = await db.collection('stocks').where(cond).limit(500).get()
  return { data: res.data, ts: Date.now() }
}

async function signals(query) {
  const limit = Math.min(Math.max(parseInt(query.limit, 10) || 50, 1), 200)
  const res = await db.collection('signals')
    .orderBy('ts', 'desc')
    .limit(limit)
    .get()
  return { data: res.data, ts: Date.now() }
}

exports.main = async (event) => {
  const method = (event.httpMethod || 'GET').toUpperCase()
  if (method === 'OPTIONS') {
    return { statusCode: 204, headers: CORS, body: '' }
  }
  const query = event.queryStringParameters || {}
  const p = (event.path || '').replace(/\/+$/, '')

  // HTTP 访问服务可能透传完整路径(/api/health)或挂载前缀之后的路径(/health)，按后缀匹配
  const routes = ['/health', '/quote', '/daily', '/stocks', '/signals']
  const hit = routes.find((r) => p.endsWith(r))
  switch (hit) {
    case '/health':
      return json({ ok: true, ts: Date.now() })
    case '/quote':
      return json(await quote(query))
    case '/daily':
      return daily(query)
    case '/stocks':
      return json(await stocks(query))
    case '/signals':
      return json(await signals(query))
    default:
      return err(404, `not found (path=${p})`)
  }
}
