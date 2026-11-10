# -*- coding: utf-8 -*-
r"""
备份长期记忆 + 对话数据（P2）。

用法：
    python scripts/backup_memory.py
    python scripts/backup_memory.py --out D:\backups\qqbot

产物：
    <out>/qqbot_data_YYYYmmdd_HHMMSS.zip
    内含 memory.db（SQLite 在线快照）、conversation_histories.json、
    runtime_settings.json、search_health.json、user_profiles/*.json

恢复：
    停 bot → 解压覆盖 qqbot/data/ 下对应文件 → 重启 bot。
    角色目录、corpus、private/.env 不在本脚本范围（体积大/含密钥），另行备份。
"""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "qqbot" / "data"


def _sqlite_snapshot(src: Path, dst: Path) -> None:
    """在线备份 SQLite（WAL 安全），不是简单复制；连接必须显式 close。"""
    s = sqlite3.connect(str(src))
    d = sqlite3.connect(str(dst))
    try:
        s.backup(d)
    finally:
        s.close()
        d.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="备份 QQ bot 记忆与对话数据")
    ap.add_argument("--out", default=str(ROOT / "backups"), help="输出目录")
    ap.add_argument("--no-profiles", action="store_true", help="不备份 user_profiles")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = out_dir / f"qqbot_data_{stamp}.zip"

    files = [
        DATA / "conversation_histories.json",
        DATA / "runtime_settings.json",
        DATA / "search_health.json",
    ]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        # 记忆库：用 SQLite backup API 做一致性快照
        mem = DATA / "memory.db"
        if mem.exists():
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                _sqlite_snapshot(mem, tmp_path)
                z.write(tmp_path, "memory.db")
            finally:
                tmp_path.unlink(missing_ok=True)
        for f in files:
            if f.exists():
                z.write(f, f.name)
        if not args.no_profiles:
            prof = DATA / "user_profiles"
            if prof.is_dir():
                for f in sorted(prof.glob("*.json")):
                    z.write(f, f"user_profiles/{f.name}")

    size = zip_path.stat().st_size
    print(f"[OK] {zip_path} ({size / 1024:.1f} KB)")
    print("恢复：停 bot → 解压覆盖 qqbot/data/ → 重启 bot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
