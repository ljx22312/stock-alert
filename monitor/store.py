"""SQLite 持久化：日线行情 + 信号日志（用于冷却去重） + 同时段量基线。"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,          -- 'YYYY-MM-DD'
    open   REAL, high REAL, low REAL, close REAL,
    volume REAL, amount REAL,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS signal_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     INTEGER NOT NULL,       -- unix 秒
    symbol TEXT NOT NULL,
    rule   TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signal_log ON signal_log(symbol, rule, ts);
CREATE TABLE IF NOT EXISTS vol_profile (
    symbol  TEXT NOT NULL,
    hm      TEXT NOT NULL,         -- 'HH:MM' 5分钟时段
    avg_vol REAL NOT NULL,         -- 同时段5分钟均量(手)
    PRIMARY KEY (symbol, hm)
);
"""


class Store:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(_SCHEMA)

    def upsert_daily(self, symbol: str, bars: list[dict]):
        """bars: [{'date','open','high','low','close','volume','amount'}, ...]"""
        self.conn.executemany(
            "INSERT OR REPLACE INTO daily_bars VALUES (?,?,?,?,?,?,?,?)",
            [(symbol, b["date"], b["open"], b["high"], b["low"], b["close"],
              b.get("volume"), b.get("amount")) for b in bars],
        )
        self.conn.commit()

    def get_daily(self, symbol: str, limit: int = 120) -> list[dict]:
        """取最近 limit 根日线，按日期升序（最新在最后）。"""
        rows = self.conn.execute(
            "SELECT date, open, high, low, close, volume, amount FROM daily_bars "
            "WHERE symbol=? ORDER BY date DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
        cols = ["date", "open", "high", "low", "close", "volume", "amount"]
        return [dict(zip(cols, r)) for r in reversed(rows)]

    def save_vol_profile(self, symbol: str, profile: dict[str, float]):
        """整体替换某只股票的同时段量基线。"""
        self.conn.execute("DELETE FROM vol_profile WHERE symbol=?", (symbol,))
        self.conn.executemany(
            "INSERT INTO vol_profile VALUES (?,?,?)",
            [(symbol, hm, v) for hm, v in profile.items()],
        )
        self.conn.commit()

    def get_vol_profile(self, symbol: str) -> dict[str, float]:
        rows = self.conn.execute(
            "SELECT hm, avg_vol FROM vol_profile WHERE symbol=?", (symbol,),
        ).fetchall()
        return dict(rows)

    def log_signal(self, symbol: str, rule: str, message: str):
        self.conn.execute(
            "INSERT INTO signal_log(ts, symbol, rule, message) VALUES (?,?,?,?)",
            (int(time.time()), symbol, rule, message),
        )
        self.conn.commit()

    def in_cooldown(self, symbol: str, rule: str, cooldown_min: int) -> bool:
        row = self.conn.execute(
            "SELECT MAX(ts) FROM signal_log WHERE symbol=? AND rule=?",
            (symbol, rule),
        ).fetchone()
        if not row or row[0] is None:
            return False
        return (time.time() - row[0]) < cooldown_min * 60

    def close(self):
        self.conn.close()
