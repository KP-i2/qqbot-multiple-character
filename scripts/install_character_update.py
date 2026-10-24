#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
角色三件套更新器 —— 把本地模型产出的 SKILL.md / persona.md / work.md 安全装进 qqbot/characters/<slug>/

设计原则：默认只预检、不写盘；确认无误再加 --apply。装之前自动备份当前版本。

用法：
  # 1) 预检（不写任何东西）
  python scripts/install_character_update.py --from <产出目录> --slug my-role

  # 2) 确认后安装（自动备份）
  python scripts/install_character_update.py --from <产出目录> --slug my-role --apply

  # 3) 深处层单独放 private/（永远不要放进角色目录）
  python scripts/install_character_update.py --from <产出目录> --slug my-role --apply --deep <deep.md路径>

退出码：0 = 通过 / 1 = 有 FAIL（不会写盘）
"""
import argparse
import datetime
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHARS_DIR = PROJECT_ROOT / "qqbot" / "characters"
PRIVATE_DIR = PROJECT_ROOT / "private"

TRIO = ("SKILL.md", "persona.md", "work.md")
FM_REQUIRED = ("name", "display_name", "description", "version")
# 这几个字段决定运行时行为，必须和线上版本一致，不能被模型改掉
FM_MUST_MATCH = ("allowed_users", "include_skill_body", "include_normal_paper", "character")

# 语料 DNA 抽查：这些是 1263 条微博提取出来的特征，模型很容易在重写时丢掉
DNA_MARKERS = {
    "persona.md": ["（？", "ㅠㅠ", "不许出现", "Layer 0", "Layer 5"],
    "work.md": ["写作规范", "善后"],
}

OK, WARN, FAIL = "[ OK ]", "[WARN]", "[FAIL]"


def parse_frontmatter(text):
    if not text.startswith("---"):
        return None
    end = text.find("---", 3)
    if end == -1:
        return None
    kv = {}
    for line in text[3:end].strip().split("\n"):
        s = line.strip()
        if not s or s.startswith("#") or ":" not in s:
            continue
        k, v = s.split(":", 1)
        kv[k.strip()] = v.strip().strip('"').strip("'")
    return kv


def load(path):
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "BOM"
    return raw.decode("utf-8"), None


def main():
    # Windows 控制台默认 GBK，遇到韩文/特殊符号会直接崩；这里降级成替换而不是抛异常
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True, help="本地模型产出目录")
    ap.add_argument("--slug", default="my-role", help="角色目录名（默认 my-role）")
    ap.add_argument("--apply", action="store_true", help="确认安装（不加则只预检）")
    ap.add_argument("--deep", default=None, help="深处层文件，会被放到 private/ 而不是角色目录")
    args = ap.parse_args()

    src = Path(args.src).expanduser().resolve()
    dst = CHARS_DIR / args.slug
    problems, warnings = [], []

    print("=" * 68)
    print("角色三件套更新器")
    print("  来源 :", src)
    print("  目标 :", dst)
    print("  模式 :", "安装（会备份）" if args.apply else "预检（不写盘）")
    print("=" * 68)

    if not src.is_dir():
        print(FAIL, "来源目录不存在")
        return 1
    if not dst.is_dir():
        print(FAIL, f"目标角色目录不存在：{dst}")
        return 1

    # ---------- 1. 文件齐不齐 ----------
    print("\n[1/6] 文件完整性")
    texts = {}
    for name in TRIO:
        f = src / name
        if not f.exists():
            problems.append(f"缺少 {name}")
            print(FAIL, name, "缺失")
            continue
        try:
            text, bom = load(f)
        except UnicodeDecodeError as e:
            problems.append(f"{name} 不是 UTF-8：{e}")
            print(FAIL, name, "编码不是 UTF-8")
            continue
        texts[name] = text
        note = "（含 BOM，会去掉）" if bom else ""
        print(OK, f"{name:<11} {len(text):>6} 字符{note}")

    if problems:
        print("\n预检未通过，先修文件。")
        return 1

    # ---------- 2. frontmatter 字段 ----------
    print("\n[2/6] SKILL.md frontmatter")
    fm = parse_frontmatter(texts["SKILL.md"])
    if fm is None:
        problems.append("SKILL.md 没有合法的 frontmatter")
        print(FAIL, "frontmatter 缺失或格式错误")
    else:
        for k in FM_REQUIRED:
            if k in fm:
                print(OK, f"{k} = {fm[k][:60]}")
            else:
                problems.append(f"frontmatter 缺字段 {k}")
                print(FAIL, f"缺字段 {k}")

        cur_fm = parse_frontmatter(load(dst / "SKILL.md")[0]) or {}
        for k in FM_MUST_MATCH:
            if k not in cur_fm:
                continue
            if fm.get(k) != cur_fm.get(k):
                warnings.append(f"{k} 被改动：{cur_fm.get(k)!r} -> {fm.get(k)!r}")
                print(WARN, f"{k} 与线上不一致：{cur_fm.get(k)!r} -> {fm.get(k)!r}")
            else:
                print(OK, f"{k} 保持一致 = {fm[k]}")

    # ---------- 3. 结构 ----------
    print("\n[3/6] 结构抽查")
    for i in range(6):
        tag = f"## Layer {i}"
        if tag in texts["persona.md"]:
            print(OK, f"persona.md 有 {tag}")
        else:
            warnings.append(f"persona.md 缺 {tag}")
            print(WARN, f"persona.md 缺 {tag}")
    if "## Correction Log" in texts["persona.md"] or "## Correction Log" in texts["work.md"]:
        print(OK, "Correction Log 在")
    else:
        warnings.append("两个文件都没有 Correction Log")
        print(WARN, "Correction Log 缺失")

    # ---------- 4. 语料 DNA ----------
    print("\n[4/6] 语料 DNA 抽查（模型最容易丢的部分）")
    for name, marks in DNA_MARKERS.items():
        for m in marks:
            if m in texts[name]:
                print(OK, f"{name} 含 {m!r}")
            else:
                warnings.append(f"{name} 丢了标记 {m!r}——确认是有意删的")
                print(WARN, f"{name} 不含 {m!r}")

    # ---------- 5. 槽位 / 目录卫生 ----------
    print("\n[5/6] 槽位与目录卫生")
    slots = sum(t.count("<SLOT") for t in texts.values())
    if slots:
        print(WARN, f"还有 {slots} 个 <SLOT> 没填（确认是不是故意的）")
        warnings.append(f"残留 {slots} 个 <SLOT>")
    else:
        print(OK, "没有残留 <SLOT>")

    stray = [f.name for f in dst.glob("*.md") if f.name not in TRIO]
    if stray:
        print(WARN, f"角色目录已有额外 *.md：{stray} —— 它们会被无条件注入 prompt")
    extra_src = [f.name for f in src.glob("*.md") if f.name not in TRIO]
    if extra_src:
        print(WARN, f"产出目录里有额外 *.md：{extra_src} —— 不会被安装，请自己确认去处")
    for bad in ("deep.md", "deep.md.template"):
        if (dst / bad).exists():
            print(WARN, f"角色目录里出现了 {bad} —— 深处层必须放在 private/")

    # ---------- 6. 差异概览 ----------
    print("\n[6/6] 与线上版本的差异")
    for name in TRIO:
        old = load(dst / name)[0] if (dst / name).exists() else ""
        delta = len(texts[name]) - len(old)
        print(f"  {name:<11} {len(old):>6} -> {len(texts[name]):>6} 字符  ({delta:+d})")

    # ---------- 安装 ----------
    print("\n" + "=" * 68)
    if problems:
        print("预检未通过：")
        for p in problems:
            print("  -", p)
        return 1
    if warnings:
        print("警告（不影响安装，但要自己确认）：")
        for w in warnings:
            print("  -", w)

    if not args.apply:
        print("\n预检通过。加 --apply 才会真正写盘。")
        return 0

    cur_ver = (parse_frontmatter(load(dst / "SKILL.md")[0]) or {}).get("version", "unknown")
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = dst / f"backup_v{cur_ver}_{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    for name in TRIO:
        if (dst / name).exists():
            shutil.copy2(dst / name, backup / name)
    print("\n已备份当前版本 ->", backup)

    for name in TRIO:
        (dst / name).write_text(texts[name], encoding="utf-8", newline="\n")
        print("已写入", dst / name)

    if args.deep:
        deep_src = Path(args.deep).expanduser().resolve()
        if not deep_src.exists():
            print(FAIL, f"--deep 指定的文件不存在：{deep_src}")
            return 1
        PRIVATE_DIR.mkdir(exist_ok=True)
        target = PRIVATE_DIR / f"{args.slug}_deep.md"
        shutil.copy2(deep_src, target)
        print("深处层已放到", target, "（不在角色目录里，不会被自动注入）")

    print("\n下一步：Dashboard -> 技能管理 -> 重载技能，然后私聊 /reset")
    return 0


if __name__ == "__main__":
    sys.exit(main())
