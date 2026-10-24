"""
Dashboard 路由共享常量与工具（2026-08-30 拆分自 main.py）

所有 router 从这里拿路径常量、安全校验、token 比较，避免循环 import。
"""
import logging
import os
import re
import secrets
from pathlib import Path

from fastapi import HTTPException

logger = logging.getLogger("dashboard")

# ── 路径常量 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # skill_communication/
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"  # qqbot/dashboard/static
PRIVATE_DIR = PROJECT_ROOT / "private"
COOKIES_FILE = PRIVATE_DIR / "cookies.json"
TWITTER_COOKIES_FILE = PRIVATE_DIR / "twitter_cookies.json"
TWITTER_SCRIPT = PROJECT_ROOT / "scripts" / "fetch_twitter.py"
TWITTER_CORPUS_DIR = PROJECT_ROOT / "corpus"
DOCS_DIR = PROJECT_ROOT / "docs"
README_FILE = PROJECT_ROOT / "README.md"
QQBOT_DIR = PROJECT_ROOT / "qqbot"

# ── 安全：Token 认证 ──
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")
if not DASHBOARD_TOKEN:
    DASHBOARD_TOKEN = secrets.token_urlsafe(16)
    logger.warning("DASHBOARD_TOKEN not set in .env — generated random token: %s", DASHBOARD_TOKEN)
_AUTH_ENABLED = bool(DASHBOARD_TOKEN and DASHBOARD_TOKEN.strip())


def token_ok(token: str | None) -> bool:
    """常量时间比较 token（防 timing attack）；未开启鉴权时直接放行"""
    if not _AUTH_ENABLED:
        return True
    return secrets.compare_digest(token or "", DASHBOARD_TOKEN)


def _safe_name(name: str) -> bool:
    """校验名称是否安全（只允许字母数字下划线横线）"""
    return bool(re.match(r'^[a-zA-Z0-9_\-]+$', name))


def _safe_path_component(s: str) -> bool:
    """校验路径组件是否安全（无路径遍历）"""
    return '..' not in s and '/' not in s and '\\' not in s and s.strip()


def _check_upload_size(content: bytes, max_mb: int = 10):
    """检查上传文件大小"""
    if len(content) > max_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File too large (max {max_mb}MB)")
