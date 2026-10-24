"""
帮助文档：实时读取 README.md + docs/*.md
（2026-08-30 拆分自 main.py）
"""
import asyncio
import time
from pathlib import Path

from fastapi import APIRouter

from ._shared import DOCS_DIR, PROJECT_ROOT, README_FILE, logger

router = APIRouter()

_DOCS_TITLE_OVERRIDES = {
    "README.md": "项目说明",
}


def _list_docs() -> list[dict]:
    """列出可用文档：根 README.md + docs/*.md"""
    docs: list[dict] = []
    if README_FILE.exists():
        st = README_FILE.stat()
        docs.append({
            "name": "README.md",
            "title": _DOCS_TITLE_OVERRIDES.get("README.md", "README"),
            "path_kind": "readme",
            "size_kb": round(st.st_size / 1024, 1),
            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
        })
    if DOCS_DIR.exists():
        for f in sorted(DOCS_DIR.glob("*.md")):
            if not f.is_file():
                continue
            st = f.stat()
            docs.append({
                "name": f"docs/{f.name}",
                "title": f.stem,
                "path_kind": "docs_md",
                "size_kb": round(st.st_size / 1024, 1),
                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
            })
    return docs


def _resolve_doc_path(name: str) -> Path | None:
    """将 API 传入的 name 解析为绝对路径；不允许路径遍历"""
    if not name or ".." in name or name.startswith(("/", "\\")):
        return None
    if name in ("README.md", "README"):
        return README_FILE
    if name.startswith("docs/"):
        return PROJECT_ROOT / name
    return None


def _read_doc(name: str) -> dict:
    """读取单个文档内容"""
    path = _resolve_doc_path(name)
    if path is None:
        return {"ok": False, "msg": "Invalid doc name（仅支持 README.md 和 docs/*.md）"}
    try:
        resolved = path.resolve()
        readme_root = README_FILE.resolve()
        docs_root = DOCS_DIR.resolve()
        in_readme = (resolved == readme_root)
        in_docs = (resolved.parent == docs_root)
        if not (in_readme or in_docs):
            return {"ok": False, "msg": "Path traversal blocked"}
    except Exception:
        return {"ok": False, "msg": "Invalid path"}
    if not resolved.exists() or not resolved.is_file():
        return {"ok": False, "msg": f"文档不存在: {name}"}
    if resolved.suffix.lower() != ".md":
        return {"ok": False, "msg": "仅支持 .md 文件"}
    try:
        content = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = resolved.read_text(encoding="gbk")
        except Exception as e:
            return {"ok": False, "msg": f"读取失败: {e}"}
    except Exception as e:
        return {"ok": False, "msg": f"读取失败: {e}"}
    st = resolved.stat()
    return {
        "ok": True,
        "name": name,
        "title": _DOCS_TITLE_OVERRIDES.get(name, resolved.stem),
        "content": content,
        "size_kb": round(st.st_size / 1024, 1),
        "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
    }


@router.get("/api/docs")
async def api_list_docs():
    return await asyncio.get_event_loop().run_in_executor(None, _list_docs)


@router.get("/api/docs/{name:path}")
async def api_get_doc(name: str):
    return await asyncio.get_event_loop().run_in_executor(None, _read_doc, name)
