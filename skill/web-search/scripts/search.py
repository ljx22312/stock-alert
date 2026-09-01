#!/usr/bin/env python3
"""Web search via Bocha or Tavily, stdlib only. Backend chosen by env key."""
import json, os, sys, urllib.request

def post(url, payload, headers):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())

def bocha(query, n):
    d = post("https://open.bochaai.com/v1/web-search",
             {"query": query, "freshness": "noLimit", "summary": True, "count": n},
             {"Authorization": "Bearer " + os.environ["BOCHA_API_KEY"]})
    pages = (d.get("data") or {}).get("webPages", {}).get("value", [])
    return [{"title": p.get("name"), "url": p.get("url"), "snippet": p.get("summary") or p.get("snippet", "")}
            for p in pages[:n]]

def tavily(query, n):
    d = post("https://api.tavily.com/search",
             {"query": query, "max_results": n, "search_depth": "basic"},
             {"Authorization": "Bearer " + os.environ["TAVILY_API_KEY"]})
    return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content", "")}
            for r in d.get("results", [])[:n]]

def main():
    query = sys.argv[1]
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 6
    try:
        if os.environ.get("BOCHA_API_KEY"):
            results = bocha(query, n)
        elif os.environ.get("TAVILY_API_KEY"):
            results = tavily(query, n)
        else:
            print(json.dumps({"error": "搜索未配置：请设置 BOCHA_API_KEY 或 TAVILY_API_KEY 环境变量"}, ensure_ascii=False))
            sys.exit(2)
        json.dump({"query": query, "results": results}, sys.stdout, ensure_ascii=False, indent=1)
        print()
    except Exception as e:
        print(json.dumps({"error": f"搜索失败: {e}"}, ensure_ascii=False))
        sys.exit(1)

if __name__ == "__main__":
    main()
