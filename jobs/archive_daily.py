"""每日数据归档：把当天有新增/变更的数据文件打包成 zip。

crontab（交易日 18:10 执行，晚于日线入库 15:35 / 数据同步 15:45 / 行业 15:55 / 宏观 16:20）:
  10 18 * * 1-5  cd /home/admin/stock-alert && python3 jobs/archive_daily.py >> logs/archive.log 2>&1

归档内容：data/ 与 logs/ 下当天修改过的所有文件（按 mtime 过滤），
包括盘中轨迹 CSV（data/intraday/）、SQLite 库、行业/宏观当日更新的 CSV、当天日志。
历史大文件（data/downloads/ 里未变更的）会被 mtime 过滤自动排除。
输出：data/daily_archive/YYYYMMDD.zip（保留 data/ logs/ 相对路径结构）。
"""
from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_DIR = ROOT / "data" / "daily_archive"
SCAN_DIRS = [ROOT / "data", ROOT / "logs"]

# 配置与凭据不进归档（zip 若日后上传飞书/网盘，避免泄漏 token）
EXCLUDE_FILES = {"config.json", "cloudbaserc.json", "cloud_sync.json",
                 "cloud_sync.state.json", "monitor.pid"}
EXCLUDE_PARTS = {"daily_archive", "__pycache__"}


def files_of_day(day0: datetime) -> list[Path]:
    out: list[Path] = []
    for scan in SCAN_DIRS:
        if not scan.exists():
            continue
        for p in scan.rglob("*"):
            if not p.is_file():
                continue
            if p.name in EXCLUDE_FILES or p.suffix in (".pid", ".pyc"):
                continue
            if any(part in EXCLUDE_PARTS for part in p.relative_to(ROOT).parts):
                continue
            if datetime.fromtimestamp(p.stat().st_mtime) >= day0:
                out.append(p)
    return out


def main():
    now = datetime.now()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    files = files_of_day(day0)
    if not files:
        print(f"{now:%Y-%m-%d %H:%M:%S} 当天无数据变更，跳过归档")
        return
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    zpath = ARCHIVE_DIR / f"{now:%Y%m%d}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(files):
            z.write(p, p.relative_to(ROOT))
    size_kb = zpath.stat().st_size / 1024
    print(f"{now:%Y-%m-%d %H:%M:%S} 归档 {len(files)} 个文件 -> {zpath.name} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
