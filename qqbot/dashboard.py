"""QQ Bot Dashboard 启动入口"""
import sys

# 修复 venv 子进程问题（同 bot.py）
if hasattr(sys, "_base_executable") and sys._base_executable != sys.executable:
    sys._base_executable = sys.executable

import uvicorn

if __name__ == "__main__":
    print("=" * 50)
    print("  QQ Bot Dashboard")
    print("  http://localhost:8501")
    print("=" * 50)
    uvicorn.run(
        "dashboard.main:app",
        host="0.0.0.0",
        port=8501,
        reload=False,
        log_level="info",
    )
