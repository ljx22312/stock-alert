/**
 * ingest：本地 stock-alert 向云数据库写入的唯一个人口。
 *
 * 鉴权：请求头 x-sync-token（或 ?token=）必须等于函数环境变量 SYNC_TOKEN。
 * 写入：POST JSON { collection, docs: [...] }，按集合规则用固定 _id upsert：
 *   - daily_bars   _id = `${symbol}_${date}`
 *   - snapshots    _id = `${symbol}`（只保留最新快照，覆盖式）
 *   - stocks       _id = `${symbol}`
 *   - vol_profile  _id = `${symbol}_${hm}`
 *   - signals      _id = `${symbol}_${rule}_${ts}`
 * GET /ingest 返回健康状态（无需令牌）。
 */
const cloudbase = require('@cloudbase/node-sdk')

const app = cloudbase.init({ env: cloudbase.SYMBOL_CURRENT_ENV })
const db = app.database()

const ALLOWED = new Set(['daily_bars', 'snapshots', 'stocks', 'signals', 'vol_profile'])
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type,x-sync-token',
}

function makeId(collection, doc) {
  switch (collection) {
    case 'daily_bars':
      return `${doc.symbol}_${doc.date}`
    case 'snapshots':
    case 'stocks':
      return String(doc.symbol)
    case 'vol_profile':
      return `${doc.symbol}_${doc.hm}`
    case 'signals':
      return doc._id || `${doc.symbol}_${doc.rule}_${doc.ts}`
    default:
      return String(doc._id)
  }
}

async function ensureCollection(name) {
  try {
    await db.createCollection(name)
  } catch (e) {
    // 已存在则忽略（createCollection 对已存在集合报错）
  }
}

async function upsertAll(collection, docs) {
  const idColl = db.collection(collection)
  const failed = []
  // 分批并发，每批 20 个
  for (let i = 0; i < docs.length; i += 20) {
    const batch = docs.slice(i, i + 20)
    const results = await Promise.allSettled(
      batch.map(async (doc) => {
        // 去掉可能为 undefined 的字段，保证 JSON 合法性
        const data = JSON.parse(JSON.stringify(doc))
        delete data._id
        await idColl.doc(makeId(collection, doc)).set(data)
      })
    )
    results.forEach((r, j) => {
      if (r.status === 'rejected') {
        failed.push({ doc: batch[j].symbol || batch[j].date, err: String(r.reason && r.reason.message || r.reason) })
      }
    })
  }
  return { upserted: docs.length - failed.length, failed }
}

exports.main = async (event) => {
  const method = (event.httpMethod || 'GET').toUpperCase()
  const headers = event.headers || {}
  const query = event.queryStringParameters || {}

  if (method === 'GET') {
    return {
      statusCode: 200,
      headers: CORS,
      body: JSON.stringify({ ok: true, service: 'ingest', env: process.env.TCB_ENV || null, ts: Date.now() }),
    }
  }

  const token = headers['x-sync-token'] || query.token
  if (!process.env.SYNC_TOKEN || token !== process.env.SYNC_TOKEN) {
    return { statusCode: 401, headers: CORS, body: JSON.stringify({ error: 'unauthorized' }) }
  }

  let body = event.body
  if (typeof body === 'string') {
    try { body = JSON.parse(body) } catch (e) { /* 保持原样 */ }
  }
  const { collection, docs } = body || {}
  if (!collection || !ALLOWED.has(collection)) {
    return { statusCode: 400, headers: CORS, body: JSON.stringify({ error: `collection must be one of ${[...ALLOWED].join(',')}` }) }
  }
  if (!Array.isArray(docs) || docs.length === 0) {
    return { statusCode: 200, headers: CORS, body: JSON.stringify({ ok: true, upserted: 0 }) }
  }

  await ensureCollection(collection)
  const result = await upsertAll(collection, docs)
  return {
    statusCode: 200,
    headers: CORS,
    body: JSON.stringify({ ok: true, upserted: result.upserted, failed: result.failed.slice(0, 20) }),
  }
}
