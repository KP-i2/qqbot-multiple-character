#!/usr/bin/env python3
"""
sanitize.py -- 脱敏脚本(本地 master -> release/2026-07-07 -> 推公开版)

两层脱敏:
  1. 内容层:扫描所有文本文件,匹配敏感 pattern 替换为占位符
  2. 文件层:基于黑名单删除不该进包的文件/目录(就算 .gitignore 没兜住也强制删)

三种模式:
  --dry-run     只打印会改/删啥,不实际动手
  --apply       真跑(改文件 + 删文件 + 自检)
  --check-only  只跑自检(self-check),已经跑过 apply 的工作区用来复核

幂等:可重复执行,结果不变。
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

# ──────────────────────────────────────────────
# 配置(敏感规则 + 黑名单)
# ──────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent  # 项目根(scripts/sanitize.py -> .. -> repo root)

# 内容层 -- regex 替换
CONTENT_RULES = [
    # API key 类
    (r"sk-[A-Za-z0-9_\-]{20,}", "<REDACTED_API_KEY>", "sk-xxx API key"),
    (r"sk-cp-[A-Za-z0-9_\-]{20,}", "<REDACTED_API_KEY>", "sk-cp-xxx API key"),

    # 本地已知真 QQ 号(管理员/OneBot)
    (r"\b2972494435\b", "<ADMIN_QQ_1>", "管理员 2972494435"),
    (r"\b2361850615\b", "<ADMIN_QQ_2>", "管理员 2361850615"),
    (r"\b3240436320\b", "<ONEBOT_QQ>", "OneBot 3240436320"),

    # 真推主 handle(normal-paper 已黑名单剔,但源码兜底)
    (r"@jiemei[_]?rose", "@<HANDLE>", "Twitter @jiemei_rose"),
    (r"@kimikyun_momo",  "@<HANDLE>", "Twitter @kimikyun_momo"),
    (r"@skingfd",        "@<HANDLE>", "Twitter @skingfd"),
    (r"@starwink",       "@<HANDLE>", "Twitter @starwink"),
    (r"@wakusei_vortex", "@<HANDLE>", "Twitter @wakusei_vortex"),
    (r"@skyfire[_a-z0-9]*", "@<HANDLE>", "Twitter @skyfire*"),
]

# 文件层 -- 整目录黑名单(.gitignore 没拦到的也强制剔)
BLACKLIST_DIRS = [
    "skill_qqbot",
    "snowluma",
    "napcat",
    "private",
    "archive",
    "assets",
    "photo",
    "logs",                # 顶层 logs/
    "corpus",
    ".runtime",
    ".pids",
    "qqbot/data",
    "qqbot/logs",
    "qqbot/napcat",
    "__pycache__",
]

# 文件层 -- 单文件黑名单
BLACKLIST_FILES = [
    "_bat_err.log",
    "_bat_out.log",
    "qqbot/snowluma_onebot_config.json",
    "qqbot/plugins/char_chat/_diag.js",
    "docs/snowluma-websocket-config.png",
]

# 文本文件后缀(只对这些做内容扫描,二进制定跳过)
TEXT_EXTS = {
    ".py", ".md", ".txt", ".yml", ".yaml", ".json", ".toml",
    ".bat", ".ps1", ".sh", ".html", ".css", ".js", ".ts",
    ".env", ".example", ".cfg", ".ini", ".log",
}
TEXT_NAMES = {".gitignore", ".gitkeep", "Dockerfile", "LICENSE", "MANIFEST.in"}

# 扫描跳过的路径(virtualenv/git/二进制 + 反正要黑名单删的目录)
# 是 path 前缀匹配(含末尾 / 检查),不是 part 匹配,避免误伤其他目录
SCAN_SKIP_PATHS = (
    ".git", "__pycache__", "skill_qqbot", "snowluma",
    "napcat", "private", "archive", "assets", "photo",
    "logs", "corpus", ".runtime", ".pids",
    "qqbot/data", "qqbot/logs", "qqbot/napcat",
    "lib",  # 嵌入式 venv 内 site-packages (skill_qqbot/Lib/site-packages)
)

# 扫描跳过的具体文件(绝对不能替换自身 / 文档性 example 原样保留)
SKIP_FILES = {
    "scripts/sanitize.py",   # 自身含规则样本,自我脱敏会破坏正则
}
SKIP_FILE_SUFFIXES = {
    ".env.example",   # 文档性占位符,原样保留
}


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

def _is_text(p: Path) -> bool:
    return p.suffix.lower() in TEXT_EXTS or p.name in TEXT_NAMES


def _is_under(rel_str: str, prefix: str) -> bool:
    return rel_str == prefix or rel_str.startswith(prefix + "/")


def collect_scan_files(root: Path) -> list[Path]:
    """收集所有需要做内容扫描的文本文件。"""
    out: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        rel_str = rel.as_posix()
        # 跳过特定目录(path 前缀匹配)
        if any(_is_under(rel_str, skip) for skip in SCAN_SKIP_PATHS):
            continue
        # 跳过特定文件(规则不能扫自己)
        if rel_str in SKIP_FILES:
            continue
        # 跳过 example 文档
        if any(rel_str.endswith(suf) for suf in SKIP_FILE_SUFFIXES):
            continue
        # 仅文本
        if _is_text(p):
            out.append(p)
    return out


def apply_content_rules(text: str):
    """应用全部内容替换规则。返回 (new_text, [变化描述])。"""
    changes: list[str] = []
    for pattern, repl, desc in CONTENT_RULES:
        new_text, n = re.subn(pattern, repl, text)
        if n > 0:
            changes.append(f"{desc}x{n}")
        text = new_text
    return text, changes


def remove_blacklisted(root: Path, *, dry: bool) -> list[str]:
    """删除所有黑名单里的目录/文件。返回操作描述列表。"""
    actions: list[str] = []
    for d in BLACKLIST_DIRS:
        p = root / d
        if p.exists():
            actions.append(f"RM DIR  {d}")
            if not dry:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    try:
                        p.unlink()
                    except OSError:
                        pass
    for f in BLACKLIST_FILES:
        p = root / f
        if p.exists():
            actions.append(f"RM FILE {f}")
            if not dry:
                try:
                    p.unlink()
                except OSError:
                    pass
    return actions


def self_check(root: Path) -> list[str]:
    """自检:脱敏完成后跑一遍全部 regex,零命中才通过。"""
    findings: list[str] = []
    files = collect_scan_files(root)
    for pattern, _, desc in CONTENT_RULES:
        for p in files:
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if re.search(pattern, text):
                findings.append(f"FOUND  {desc:<35} @ {p.relative_to(root)}")
    return findings


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="脱敏脚本:本地 master -> release 分支")
    ap.add_argument("--dry-run",     action="store_true", help="只打印会改/删啥,不实际动手")
    ap.add_argument("--apply",       action="store_true", help="真跑(改文件+删文件+自检)")
    ap.add_argument("--check-only",  action="store_true", help="只跑自检")
    ap.add_argument("--summary",     action="store_true", help="只输出汇总,不一一列出")
    args = ap.parse_args()

    if not (args.dry_run or args.apply or args.check_only):
        ap.error("至少传 --dry-run 或 --apply 或 --check-only")

    # 单跑自检
    if args.check_only:
        findings = self_check(ROOT)
        if findings:
            print("❌ 自检未通过:")
            for f in findings:
                print(f"  {f}")
            return 1
        print("✅ 自检通过:零敏感命中")
        return 0

    mode = "DRY-RUN" if args.dry_run else "APPLY"
    print(f"\n{'='*60}\n  sanitize.py  [{mode}]\n{'='*60}\n")

    # 1. 内容层扫描
    print("[1/2] 内容层扫描中...")
    modified: list[tuple[Path, list[str]]] = []
    for p in collect_scan_files(ROOT):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        new_text, changes = apply_content_rules(text)
        if changes:
            modified.append((p, changes))
            rel = p.relative_to(ROOT)
            if not args.summary or args.dry_run:
                tag = "would-edit" if args.dry_run else "✏️ "
                print(f"  {tag}  {rel}  ({'; '.join(changes)})")
            elif args.apply:
                print(f"  ✏️  {rel}")

    if args.apply:
        for p, _ in modified:
            new_text, _ = apply_content_rules(p.read_text(encoding="utf-8", errors="ignore"))
            p.write_text(new_text, encoding="utf-8")

    # 2. 文件层黑名单
    print(f"\n[2/2] 文件层黑名单{' (不会动)' if args.dry_run else ''}...")
    actions = remove_blacklisted(ROOT, dry=args.dry_run)
    for a in actions:
        prefix = "  would-rm" if args.dry_run else "  🗑️ "
        print(f"{prefix}  {a}")

    # 汇总
    print(f"\n{'─'*60}")
    print(f"  内容层: {len(modified)} 个文件被改")
    print(f"  文件层: {len(actions)} 项删除")
    print(f"{'─'*60}")

    # 自检(仅在 apply 跑完后)
    if args.apply:
        print("\n[自检] 重新扫描...")
        findings = self_check(ROOT)
        if findings:
            print("❌ 自检未通过:")
            for f in findings:
                print(f"  {f}")
            return 1
        print("✅ 自检通过:零敏感命中")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
