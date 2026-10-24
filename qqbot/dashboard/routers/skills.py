"""
Skill 管理：列表/编辑/创建/蒸馏/头像/AI 特征整合/删除
（2026-08-30 拆分自 main.py）
"""
import asyncio
import json
import re
import time
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile

from .. import skill_manager, weibo_fetcher
from ._shared import PROJECT_ROOT, STATIC_DIR, _check_upload_size, _safe_name, logger

router = APIRouter()


# ── Skill 管理 ──
@router.get("/api/skills")
async def api_list_skills():
    return skill_manager.list_skills()


@router.get("/api/skills/{name}")
async def api_skill_detail(name: str):
    if not _safe_name(name):
        return {"ok": False, "msg": "Invalid name"}
    return skill_manager.get_skill_content(name)


@router.post("/api/skills/create")
async def api_create_skill(
    name: str = Form(...),
    display_name: str = Form(""),
    description: str = Form(""),
    version: str = Form("1.0.0"),
    persona: str = Form(""),
    work: str = Form(""),
):
    if not _safe_name(name):
        return {"ok": False, "msg": "Invalid name"}
    if not display_name:
        display_name = name
    result = skill_manager.create_skill(name, display_name, description, persona, work, version)
    if result.get("ok"):
        logger.info(f"Skill created: {name} ({display_name})")
    return result


# ── 蒸馏管理（带并发保护）──
_running_retrains: set[str] = set()
_retrain_tasks: dict[str, asyncio.Task] = {}  # 保持强引用，防止 GC 销毁


@router.post("/api/skills/{name}/retrain")
async def api_retrain_skill(name: str, character: str = "celebrity", research_profile: str = "budget-friendly"):
    if not _safe_name(name):
        return {"ok": False, "msg": "Invalid name"}
    # 并发保护：同一 skill 不允许同时蒸馏
    if name in _running_retrains:
        return {"ok": False, "msg": f"蒸馏正在进行中，请等待完成后再试"}
    _running_retrains.add(name)
    task = asyncio.create_task(_run_retrain(name, character, research_profile))
    _retrain_tasks[name] = task
    task.add_done_callback(lambda t, n=name: _retrain_tasks.pop(n, None))
    logger.info(f"Retrain started: {name} ({character}/{research_profile})")
    return {"ok": True, "msg": f"蒸馏已启动 ({character}/{research_profile})，请在进度面板查看"}


async def _run_retrain(name, character, research_profile):
    """Background wrapper for retrain_skill"""
    try:
        result = await weibo_fetcher.retrain_skill(name, character, research_profile)
        if result.get("ok"):
            trigger = PROJECT_ROOT / ".reload_skills_trigger"
            trigger.write_text(f"reload from dashboard at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            # retrain_skill 返回了错误字典（非异常），需要同步到进度追踪
            err_msg = result.get("msg", "未知错误")
            logger.error(f"Retrain '{name}' returned error: {err_msg}")
            weibo_fetcher._update_progress(name, "error", err_msg, done=True, ok=False)
    except Exception as e:
        logger.error(f"Retrain '{name}' failed: {e}")
        weibo_fetcher._update_progress(name, "error", str(e), done=True, ok=False)
    finally:
        _running_retrains.discard(name)


@router.get("/api/skills/{name}/retrain-progress")
async def api_retrain_progress(name: str):
    if not _safe_name(name):
        return {"ok": False, "msg": "Invalid name"}
    progress = weibo_fetcher.get_retrain_progress(name)
    progress["is_running"] = name in _running_retrains
    return progress


@router.post("/api/bot/reload")
async def api_bot_reload():
    """触发 bot 重新加载 skill"""
    trigger = PROJECT_ROOT / ".reload_skills_trigger"
    trigger.write_text(f"manual reload at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("Manual bot reload triggered")
    return {"ok": True, "msg": "Reload trigger sent. Bot will reload skills on next message."}


@router.put("/api/skills/{name}/{filename}")
async def api_update_skill_file(name: str, filename: str, request: Request):
    if not _safe_name(name):
        return {"ok": False, "msg": "Invalid skill name"}
    # 文件名允许包含点（如 persona.md）
    if not re.match(r'^[a-zA-Z0-9_\-\.]+$', filename) or '..' in filename:
        return {"ok": False, "msg": "Invalid filename"}
    # 路径包含检查：确保解析后仍在 skill 目录内
    skill_dir = (PROJECT_ROOT / "qqbot" / "characters" / name).resolve()
    target = (skill_dir / filename).resolve()
    if not str(target).startswith(str(skill_dir)):
        return {"ok": False, "msg": "Invalid file path"}
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "msg": "Invalid JSON body"}
    result = skill_manager.update_skill_file(name, filename, body.get("content", ""))
    if result.get("ok"):
        # 保存即重载：写入 trigger 文件让 bot 热加载
        trigger = PROJECT_ROOT / ".reload_skills_trigger"
        trigger.write_text(f"auto reload after {name}/{filename} saved at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        result["reloaded"] = True
        logger.info(f"Skill file updated: {name}/{filename}")
    return result


@router.post("/api/skills/{name}/integrate-trait")
async def api_integrate_trait(name: str, request: Request):
    """AI 智能整合：输入特征描述，调用 DeepSeek 合并到 persona.md"""
    if not _safe_name(name):
        return {"ok": False, "msg": "无效的技能名称"}
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "msg": "无效的 JSON 请求"}
    trait = (body.get("trait") or "").strip()
    target_file = body.get("target", "persona.md")
    if target_file not in ("persona.md", "work.md"):
        target_file = "persona.md"
    if not trait:
        return {"ok": False, "msg": "请输入特征描述"}
    # 检查技能是否存在
    skill_dir = PROJECT_ROOT / "qqbot" / "characters" / name
    if not skill_dir.exists():
        return {"ok": False, "msg": f"技能 '{name}' 不存在"}
    # 读取当前文件内容
    target_path = skill_dir / target_file
    if not target_path.exists():
        current_content = ""
    else:
        current_content = target_path.read_text(encoding="utf-8")
    # 调用 DeepSeek 智能整合
    ok, result_text = await weibo_fetcher._deepseek_call(
        messages=[
            {"role": "system", "content": (
                "你是一个角色人设文档编辑专家。你的任务是将用户提供的新特征描述，"
                "智能整合到现有的角色人设文档（persona.md）中。\n\n"
                "规则：\n"
                "1. 保持现有文档的结构和风格不变\n"
                "2. 将新特征融入最合适的章节，如果没有合适的章节则追加到末尾\n"
                "3. 如果新特征与已有内容有重叠，合并而非重复\n"
                "4. 如果文档中存在「Correction Log」章节，必须在该章节中追加一条变更记录，格式为：\n"
                "   - YYYY-MM-DD: 整合了「新特征的简要摘要」，影响章节：Layer X/章节名\n"
                "   如果 Correction Log 为 (empty) 或不存在，则替换 (empty) 或新增该章节（放在文档最后）\n"
                "5. 用中文输出完整的修改后文档（不要输出 diff，输出完整内容）\n"
                "6. 如果现有文档为空，根据输入创建一份结构化的 persona 文档\n"
                "7. 只输出文档内容，不要加任何解释或前言"
            )},
            {"role": "user", "content": (
                f"当前 {target_file} 内容：\n"
                f"```\n{current_content}\n```\n\n"
                f"新特征描述：\n{trait}\n\n"
                f"请输出整合后的完整 {target_file} 内容："
            )},
        ],
        temperature=0.6,
        max_tokens=6000,
        timeout=180,
    )
    if not ok:
        return {"ok": False, "msg": f"AI 整合失败: {result_text}"}
    # 清理返回内容（去除可能的 markdown 代码块包裹）
    merged = result_text.strip()
    if merged.startswith("```"):
        first_nl = merged.index("\n") if "\n" in merged else 3
        merged = merged[first_nl + 1:]
    if merged.endswith("```"):
        merged = merged[:-3].rstrip()
    # 写入文件
    target_path.write_text(merged, encoding="utf-8")
    # 自动重载
    trigger = PROJECT_ROOT / ".reload_skills_trigger"
    trigger.write_text(f"auto reload after trait integrate on {name}/{target_file} at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Trait integrated into {name}/{target_file}")
    return {
        "ok": True,
        "msg": f"特征已整合到 {name}/{target_file}，bot 已自动重载",
        "content": merged,
        "reloaded": True,
    }


@router.get("/api/avatars")
async def api_list_avatars():
    """列出所有角色头像路径"""
    static_photo_dir = STATIC_DIR / "photo"
    result = {}
    if static_photo_dir.exists():
        for d in static_photo_dir.iterdir():
            if d.is_dir():
                # 优先使用 avatar.* 文件（新上传的），否则使用目录下第一个图片
                avatar_file = None
                fallback_file = None
                for f in d.iterdir():
                    if f.suffix.lower() in ('.jpg', '.jpeg', '.png'):
                        if f.stem.lower() == 'avatar':
                            avatar_file = f
                            break
                        elif fallback_file is None:
                            fallback_file = f
                chosen = avatar_file or fallback_file
                if chosen:
                    result[d.name] = f"/static/photo/{d.name}/{chosen.name}"
    return result


@router.post("/api/skills/{name}/avatar")
async def api_upload_avatar(name: str, file: UploadFile = File(...)):
    """上传角色头像（JPG/PNG）"""
    if not _safe_name(name):
        return {"ok": False, "msg": "无效的技能名称"}
    # 检查文件类型
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ('.jpg', '.jpeg', '.png'):
        return {"ok": False, "msg": "仅支持 JPG / PNG 格式"}
    content = await file.read()
    _check_upload_size(content, 5)
    # 保存到 photo 目录（先清理旧文件）
    photo_dir = PROJECT_ROOT / "photo" / name
    photo_dir.mkdir(parents=True, exist_ok=True)
    for old in photo_dir.iterdir():
        if old.is_file() and old.suffix.lower() in ('.jpg', '.jpeg', '.png'):
            old.unlink()
    photo_path = photo_dir / f"avatar{suffix}"
    photo_path.write_bytes(content)
    # 同步到 dashboard 静态目录（同样清理旧文件）
    static_photo_dir = STATIC_DIR / "photo" / name
    static_photo_dir.mkdir(parents=True, exist_ok=True)
    for old in static_photo_dir.iterdir():
        if old.is_file() and old.suffix.lower() in ('.jpg', '.jpeg', '.png'):
            old.unlink()
    (static_photo_dir / f"avatar{suffix}").write_bytes(content)
    logger.info(f"Avatar uploaded for skill: {name} ({len(content)} bytes)")
    return {"ok": True, "msg": f"头像上传成功 ({len(content)} bytes)"}


@router.delete("/api/skills/{name}")
async def api_delete_skill(name: str):
    if not _safe_name(name):
        return {"ok": False, "msg": "Invalid name"}
    result = await asyncio.get_event_loop().run_in_executor(None, skill_manager.delete_skill, name)
    if result.get("ok"):
        logger.info(f"Skill deleted: {name}")
    return result
