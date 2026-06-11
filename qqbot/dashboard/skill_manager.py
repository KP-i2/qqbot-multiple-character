"""Skill 管理：列表、创建、编辑、删除"""
import logging
import os
from pathlib import Path

logger = logging.getLogger("dashboard.skill_manager")

PROJECT_ROOT = Path(__file__).parent.parent.parent
SKILLS_DIR = PROJECT_ROOT / "qqbot" / "skills"
PHOTO_DIR = PROJECT_ROOT / "photo"
CORPUS_DIR = PROJECT_ROOT / "corpus"


def list_skills() -> list[dict]:
    """列出所有 Skill"""
    result = []
    if not SKILLS_DIR.exists():
        return result
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith((".", "colleague")):
            continue
        info = _read_skill_info(d)
        result.append(info)
    return result


def _read_skill_info(skill_dir: Path) -> dict:
    """读取单个 Skill 信息"""
    info = {
        "name": skill_dir.name,
        "path": str(skill_dir),
        "has_persona": (skill_dir / "persona.md").exists(),
        "has_work": (skill_dir / "work.md").exists(),
        "has_skill_md": (skill_dir / "SKILL.md").exists(),
        "display_name": "",
        "description": "",
        "version": "",
        "prompt_size": 0,
        "has_avatar": False,
        "created": "",
        "corpus_ref": (skill_dir / "corpus_ref").exists() or any(
            d.is_dir() and d.name.startswith(skill_dir.name + "_")
            for d in (PROJECT_ROOT / "corpus").iterdir()
        ) if (PROJECT_ROOT / "corpus").exists() else False,
    }

    # 创建时间（取最早文件的 mtime）
    earliest = None
    for f in skill_dir.iterdir():
        if f.is_file() and f.suffix == ".md":
            mt = f.stat().st_mtime
            if earliest is None or mt < earliest:
                earliest = mt
    if earliest:
        import time as _time
        info["created"] = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(earliest))

    # Parse SKILL.md frontmatter
    skill_md = skill_dir / "SKILL.md"
    if skill_md.exists():
        content = skill_md.read_text(encoding="utf-8")
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                for line in content[3:end].strip().split("\n"):
                    if line.startswith("display_name:"):
                        info["display_name"] = line.split(":", 1)[1].strip().strip('"').strip("'")
                    elif line.startswith("description:"):
                        info["description"] = line.split(":", 1)[1].strip().strip('"').strip("'")
                    elif line.startswith("version:"):
                        info["version"] = line.split(":", 1)[1].strip().strip('"').strip("'")

    # Calculate prompt size
    total = 0
    _counted = {"SKILL.md", "persona.md", "work.md", "starwink.md"}
    for fname in _counted:
        f = skill_dir / fname
        if f.exists():
            total += f.stat().st_size
    # Include other .md files not already counted
    for f in skill_dir.glob("*.md"):
        if f.name not in _counted:
            total += f.stat().st_size
    info["prompt_size"] = total

    # Check avatar
    photo_dir = PHOTO_DIR / skill_dir.name
    if photo_dir.exists():
        for f in photo_dir.iterdir():
            if f.suffix.lower() in (".jpg", ".jpeg", ".png") and not f.name.startswith("_"):
                info["has_avatar"] = True
                break

    if not info["display_name"]:
        info["display_name"] = info["name"]

    return info


def create_skill(name: str, display_name: str, description: str,
                 persona_content: str = "", work_content: str = "",
                 version: str = "1.0.0") -> dict:
    """创建新 Skill"""
    skill_dir = SKILLS_DIR / name
    if skill_dir.exists():
        return {"ok": False, "msg": f"Skill '{name}' already exists"}

    skill_dir.mkdir(parents=True)

    # Write SKILL.md
    skill_md = f"""---
name: {name}
display_name: "{display_name}"
description: "{description}"
version: "{version}"
user-invocable: true
argument-hint: "[task or question]"
---

# {display_name} Skill

{description}
"""
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

    # Write persona.md
    if persona_content:
        (skill_dir / "persona.md").write_text(persona_content, encoding="utf-8")

    # Write work.md
    if work_content:
        (skill_dir / "work.md").write_text(work_content, encoding="utf-8")

    return {"ok": True, "msg": f"Skill '{name}' created", "path": str(skill_dir)}


def delete_skill(name: str) -> dict:
    """删除 Skill"""
    import shutil
    skill_dir = SKILLS_DIR / name
    if not skill_dir.exists():
        return {"ok": False, "msg": f"Skill '{name}' not found"}
    try:
        shutil.rmtree(skill_dir)
    except PermissionError:
        logger.error(f"Permission denied when deleting skill dir '{name}'")
        return {"ok": False, "msg": f"删除技能目录失败: 权限不足，请检查文件权限"}
    except OSError as e:
        logger.error(f"OS error when deleting skill dir '{name}': {e}")
        return {"ok": False, "msg": f"删除技能目录失败: 系统错误 - {e}"}
    except Exception as e:
        logger.error(f"Failed to delete skill dir '{name}': {e}")
        return {"ok": False, "msg": f"删除技能目录失败: {type(e).__name__} - {e}"}
    # Also remove avatar if exists
    photo_dir = PHOTO_DIR / name
    if photo_dir.exists():
        try:
            shutil.rmtree(photo_dir)
        except PermissionError:
            logger.warning(f"Permission denied when deleting photo dir '{name}'")
            return {"ok": True, "msg": f"技能已删除，但头像目录删除失败: 权限不足"}
        except OSError as e:
            logger.warning(f"OS error when deleting photo dir '{name}': {e}")
            return {"ok": True, "msg": f"技能已删除，但头像目录删除失败: 系统错误 - {e}"}
        except Exception as e:
            logger.warning(f"Failed to delete photo dir '{name}': {e}")
            return {"ok": True, "msg": f"技能已删除，但头像目录删除失败: {type(e).__name__} - {e}"}
    return {"ok": True, "msg": f"Skill '{name}' deleted"}


def get_skill_content(name: str) -> dict:
    """获取 Skill 文件内容"""
    skill_dir = SKILLS_DIR / name
    if not skill_dir.exists():
        return {"ok": False, "msg": f"Skill '{name}' not found"}
    result = {"ok": True, "name": name, "files": {}}
    for f in skill_dir.glob("*.md"):
        try:
            content = f.read_text(encoding="utf-8")
            # 清理可能导致 JSON 序列化问题的控制字符
            content = ''.join(c for c in content if c in '\n\r\t' or c.isprintable())
            result["files"][f.name] = content
        except UnicodeDecodeError:
            result["files"][f.name] = f"[读取失败: {f.name} - 文件编码错误]"
        except PermissionError:
            result["files"][f.name] = f"[读取失败: {f.name} - 权限不足]"
        except OSError as e:
            result["files"][f.name] = f"[读取失败: {f.name} - 系统错误: {e}]"
        except Exception as e:
            result["files"][f.name] = f"[读取失败: {f.name} - {type(e).__name__}: {e}]"
    return result


def update_skill_file(name: str, filename: str, content: str) -> dict:
    """更新 Skill 中的某个文件"""
    skill_dir = SKILLS_DIR / name
    if not skill_dir.exists():
        return {"ok": False, "msg": f"Skill '{name}' not found"}
    target = skill_dir / filename
    if not target.exists():
        return {"ok": False, "msg": f"File '{filename}' not found in {name}"}
    try:
        target.write_text(content, encoding="utf-8")
    except PermissionError:
        return {"ok": False, "msg": f"更新文件失败: 权限不足，请检查文件权限"}
    except OSError as e:
        return {"ok": False, "msg": f"更新文件失败: 系统错误 - {e}"}
    except Exception as e:
        return {"ok": False, "msg": f"更新文件失败: {type(e).__name__} - {e}"}
    return {"ok": True, "msg": f"{filename} updated"}

