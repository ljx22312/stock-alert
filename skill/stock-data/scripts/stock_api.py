#!/usr/bin/env python3
"""StockDesk data CLI: cloud API + eastmoney realtime, stdlib only."""
import argparse, json, re, sys, urllib.request, urllib.parse

CLOUD_API = "https://ljx-d1gjpcu23fa094e67.service.tcloudbase.com/api"

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "stockdesk-ai/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))

def em_secid(sym):
    s = sym.strip().lower()
    if s.startswith(("sh", "sz", "bj")):
        pfx, code = s[:2], s[2:]
    else:
        code = s
        pfx = "sh" if s.startswith(("6", "5")) else "sz"
    return {"sh": "1", "sz": "0", "bj": "0"}[pfx] + "." + code

def all_symbols():
    d = get(CLOUD_API + "/stocks")
    return [x["symbol"] for x in d.get("data", []) if x.get("type") == "stock"]

def realtime_tencent(symbols):
    """腾讯行情备用接口（qt.gtimg.cn），东财被封时切换。"""
    codes = ",".join((("sh" if em_secid(s).startswith("1") else "sz") + em_secid(s)[2:]) for s in symbols)
    req = urllib.request.Request("https://qt.gtimg.cn/q=" + codes,
                                 headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
    raw = urllib.request.urlopen(req, timeout=20).read().decode("gbk", errors="replace")
    out = []
    for line in raw.split(";"):
        m = re.match(r'v_\w+="(.*)"', line.strip())
        if not m or not m.group(1):
            continue
        f = m.group(1).split("~")
        if len(f) < 20:
            continue
        out.append({"symbol": f[2], "name": f[1], "price": float(f[3] or 0),
                    "pct_change": float(f[32] or 0) if len(f) > 32 and f[32] else None,
                    "change": float(f[31] or 0) if len(f) > 31 and f[31] else None,
                    "high": float(f[33] or 0) if len(f) > 33 and f[33] else None,
                    "low": float(f[34] or 0) if len(f) > 34 and f[34] else None,
                    "open": float(f[5] or 0), "prev_close": float(f[4] or 0),
                    "volume_hand": float(f[6] or 0), "amount_wan": float(f[37] or 0) if len(f) > 37 and f[37] else None,
                    "quote_time": f[30] if len(f) > 30 else "", "source": "tencent"})
    return out

def realtime_eastmoney(symbols):
    secids = ",".join(em_secid(s) for s in symbols)
    url = ("https://push2.eastmoney.com/api/qt/ulist.np/get?secids=" + urllib.parse.quote(secids)
           + "&fields=f2,f3,f4,f12,f14,f15,f16,f17,f18&fltt=2&invt=2")
    d = get(url)
    return [{"symbol": f.get("f12"), "name": f.get("f14"), "price": f.get("f2"),
            "pct_change": f.get("f3"), "change": f.get("f4"), "high": f.get("f15"),
            "low": f.get("f16"), "open": f.get("f17"), "prev_close": f.get("f18"),
            "source": "eastmoney"}
           for f in (d.get("data") or {}).get("diff", [])]

def realtime(symbols):
    """优先东财，失败/被封自动切腾讯。"""
    try:
        r = realtime_eastmoney(symbols)
        if r:
            return r
    except Exception:
        pass
    return realtime_tencent(symbols)

def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stocks")
    q = sub.add_parser("quote"); q.add_argument("symbols", nargs="?", default="")
    r = sub.add_parser("realtime"); r.add_argument("symbols", nargs="?", default="")
    d = sub.add_parser("daily"); d.add_argument("symbol"); d.add_argument("--limit", type=int, default=120)
    s = sub.add_parser("signals"); s.add_argument("--limit", type=int, default=20)
    a = p.parse_args()

    if a.cmd == "stocks":
        res = get(CLOUD_API + "/stocks")
    elif a.cmd == "quote":
        res = get(CLOUD_API + "/quote" + ("?symbols=" + a.symbols if a.symbols else ""))
    elif a.cmd == "daily":
        res = get(CLOUD_API + f"/daily?symbol={a.symbol}&limit={min(a.limit, 1000)}")
    elif a.cmd == "signals":
        res = get(CLOUD_API + f"/signals?limit={a.limit}")
    elif a.cmd == "realtime":
        res = {"data": realtime(a.symbols.split(",") if a.symbols else all_symbols())}
    json.dump(res, sys.stdout, ensure_ascii=False, indent=1)
    print()

if __name__ == "__main__":
    main()
