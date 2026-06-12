"""
Skill 管理：加载、热重载、角色切换
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from nonebot import logger
from nonebot.message import event_preprocessor

from . import config as cfg


def _load_normal_paper() -> str:
    """加载 normal-paper/basic/ 目录下的 .md 文件作为通用背景知识（全量注入 prompt）"""
    basic_dir = cfg.NORMAL_PAPER_DIR / "basic"
    if not basic_dir.exists():
        return ""
    parts = []
    for md_file in sorted(basic_dir.glob("*.md")):
        content = md_file.read_text(encoding="utf-8").strip()
        if content:
            title = md_file.stem  # 文件名去 .md 后缀
            parts.append(f"## {title}\n{content}")
    if not parts:
        return ""
    return "# 通用背景知识\n以下是所有角色共享的领域背景资料，请在回复中自然运用这些知识。\n\n" + "\n\n".join(parts)


# 启动时加载通用背景
NORMAL_PAPER_CONTEXT = _load_normal_paper()
if NORMAL_PAPER_CONTEXT:
    logger.info(f"[normal-paper] Loaded {len(NORMAL_PAPER_CONTEXT)} chars from {cfg.NORMAL_PAPER_DIR}")


@dataclass
class Skill:
    """一个角色 Skill"""
    name: str
    path: Path
    system_prompt: str
    display_name: str = ""
    description: str = ""
    version: str = ""
    prompt_size: int = 0


def load_skill(skill_path: Path) -> Optional[Skill]:
    """从目录加载一个 Skill"""
    name = skill_path.name
    skill_md = skill_path / "SKILL.md"
    persona_md = skill_path / "persona.md"
    work_md = skill_path / "work.md"

    if not persona_md.exists() and not work_md.exists():
        return None

    # 解析 SKILL.md 获取元数据
    description = ""
    version = ""
    display_name = ""
    if skill_md.exists():
        content = skill_md.read_text(encoding="utf-8")
        # 简单解析 frontmatter
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                fm = content[3:end].strip()
                for line in fm.split("\n"):
                    if line.startswith("description:"):
                        description = line.split(":", 1)[1].strip().strip('"').strip("'")
                    elif line.startswith("version:"):
                        version = line.split(":", 1)[1].strip().strip('"').strip("'")
                    elif line.startswith("display_name:"):
                        display_name = line.split(":", 1)[1].strip().strip('"').strip("'")
    # display_name 默认用目录名
    if not display_name:
        display_name = name

    # 构建 system prompt
    role_instruction = (
        "# 角色扮演指令\n"
        "你现在要完全扮演以下角色。你不是 AI 助手，你就是这个人。\n"
        "始终保持角色的说话方式、态度和价值观。不要跳出角色，不要说'作为一个AI'之类的话。\n"
        "用中文回复，口语化，像在微博/微信聊天一样自然。\n\n"
        "# 工具使用原则（极其重要）\n"
        "你有两个搜索工具可以使用：web_search（联网搜索）和 search_corpus（语料库搜索）。\n"
        "- 当用户问到你不确定的事实、最新事件、专业领域知识、时事热点时，必须主动使用 web_search 搜索\n"
        "- 当用户明确提到「查」「搜」「找」「看看」「帮我查」「帮我搜」「了解一下」等查询意图时，必须使用 web_search\n"
        "- 当用户提到具体的团体名、人名、作品名、活动名等你不完全确定的专有名词时，必须先用 web_search 查证\n"
        "- 当用户问到与角色背景、经历、设定相关的问题时，应使用 search_corpus 查询角色资料\n"
        "- 当用户问到通用背景知识中未涵盖的领域知识（如团体信息、人物关系等）时，也应使用 search_corpus 查询共享资料库\n"
        "- 绝对不要编造搜索结果的口吻（如'搜了一下''查到了'），如果没调用搜索工具就不能假装有搜索结果\n"
        "- 宁可多搜一次也不要编造信息，保持回答的真实性和角色一致性\n"
        "- 搜索结果要自然融入回复，不要暴露搜索过程\n"
"- 当搜索结果包含链接时，必须在回复中保留完整链接（如 https://xxx），方便用户点击\n"
"- 严禁编造或猜测URL！只使用搜索结果中明确提供的链接，如果搜索结果没有链接就不要提供\n"
"- 微博用户主页URL必须是数字ID格式（如 https://weibo.com/u/1234567890），不能包含中文字符\n"
"- URL必须完整保留，不能截断（如不能写成 https://space.bilibili.com/...），必须包含完整的数字ID\n\n"
        "# 搜索查询优化（极其重要）\n"
        "生成搜索查询时遵循以下规则：\n"
        "- 如果用户提到具体名称（团体名/人名/作品名），直接搜该名称，不要加修饰词\n"
        "- 如果用户问「本周/最近/今天」的活动，查询中要包含时间词（如「2026年6月 演出」）\n"
        "- 如果用户问的是小众领域（如地下偶像），优先搜微博（中文社区更活跃）\n"
        "- 搜索词尽量简短，只写核心名称（如「阵雨电台」而非「阵雨电台 地下偶像 介绍」）\n"
        "- 不要加「官方」「是谁」「介绍」等通用修饰词，微博搜索对短关键词效果更好\n"
        "- 如果用户没说具体名称，先搜大类（如「地下偶像 演出 本周」），再根据结果搜具体名称\n"
        "# QQ表情使用\n"
        "你可以在回复中自然地使用QQ表情，格式为方括号包裹表情名。"
        "常用表情：[微笑] [撇嘴] [色] [发呆] [得意] [流泪] [害羞] [闭嘴] [睡] [大哭] "
        "[尴尬] [发怒] [调皮] [呲牙] [惊讶] [难过] [酷] [冷汗] [抓狂] [吐] [偷笑] [可爱] "
        "[白眼] [傲慢] [饥饿] [困] [惊恐] [流汗] [憨笑] [悠闲] [奋斗] [咒骂] [疑问] "
        "[嘘] [晕] [折磨] [衰] [骷髅] [敲打] [再见] [擦汗] [抠鼻] [鼓掌] [糗大了] "
        "[坏笑] [左哼哼] [右哼哼] [哈欠] [鄙视] [委屈] [快哭了] [阴险] [亲亲] [吓] "
        "[可怜] [菜刀] [西瓜] [啤酒] [篮球] [乒乓] [咖啡] [饭] [猪头] [玫瑰] [凋谢] "
        "[示爱] [爱心] [拥抱] [强] [弱] [握手] [胜利] [抱拳] [勾引] [拳头] [差劲] "
        "[爱你] [NO] [OK] [转圈] [磕头] [回头] [跳绳] [挥手] [激动] [街舞] [献吻] "
        "[左太极] [右太极] [doge] [捂脸] [笑哭] [嘿哈] [捂嘴笑] [思考] [泪奔] [笑哭] "
        "不要每句话都加，适度使用，符合角色性格和语境。"
        "如果角色性格活泼可以多用，如果角色沉稳内敛则少用或不用。\n"
    )
    parts = [role_instruction]

    # 注入通用背景知识（normal-paper/）
    if NORMAL_PAPER_CONTEXT:
        parts.append(NORMAL_PAPER_CONTEXT)

    if persona_md.exists():
        parts.append(f"# 角色人格\n{persona_md.read_text(encoding='utf-8')}")
    if work_md.exists():
        parts.append(f"# 工作能力\n{work_md.read_text(encoding='utf-8')}")

    # 加载额外参考文件（如 starwink.md 等团体背景）
    for extra_file in sorted(skill_path.glob("*.md")):
        ename = extra_file.name
        if ename in ("SKILL.md", "persona.md", "work.md"):
            continue  # 已加载
        parts.append(f"# 附加参考：{ename}\n{extra_file.read_text(encoding='utf-8')}")

    system_prompt = "\n\n---\n\n".join(parts)

    return Skill(
        name=name,
        path=skill_path,
        system_prompt=system_prompt,
        display_name=display_name,
        description=description,
        version=version,
        prompt_size=len(system_prompt),
    )


def load_all_skills() -> dict[str, Skill]:
    """加载 skills/ 目录下所有角色"""
    skills = {}
    if not cfg.SKILLS_DIR.exists():
        logger.warning(f"Skills directory not found: {cfg.SKILLS_DIR}")
        return skills

    for d in sorted(cfg.SKILLS_DIR.iterdir()):
        if d.is_dir() and not d.name.startswith((".", "_")):
            skill = load_skill(d)
            if skill:
                skills[skill.name] = skill
                logger.info(f"  [OK] Skill '{skill.name}' loaded "
                           f"(display: {skill.display_name}, "
                           f"prompt: {len(skill.system_prompt)} chars, "
                           f"v{skill.version})")
            else:
                logger.info(f"  [SKIP] '{d.name}' - no persona.md or work.md")

    return skills


# 启动时加载所有 Skill
logger.info(f"Loading skills from: {cfg.SKILLS_DIR}")
ALL_SKILLS = load_all_skills()
logger.info(f"Total skills loaded: {len(ALL_SKILLS)}")

# 默认 skill：优先使用 .env 中配置的 DEFAULT_SKILL，否则取目录排序第一个
if cfg.DEFAULT_SKILL_NAME_CONFIG and cfg.DEFAULT_SKILL_NAME_CONFIG in ALL_SKILLS:
    DEFAULT_SKILL_NAME = cfg.DEFAULT_SKILL_NAME_CONFIG
    logger.info(f"默认角色（配置指定）：{DEFAULT_SKILL_NAME}")
else:
    DEFAULT_SKILL_NAME = next(iter(ALL_SKILLS), None) if ALL_SKILLS else None
    if cfg.DEFAULT_SKILL_NAME_CONFIG:
        logger.warning(f"配置的默认角色 '{cfg.DEFAULT_SKILL_NAME_CONFIG}' 不存在，使用：{DEFAULT_SKILL_NAME}")
    else:
        logger.info(f"默认角色（自动选择）：{DEFAULT_SKILL_NAME}")


# ── Skill 热重载 ──
RELOAD_TRIGGER = cfg.QQBOT_DIR.parent / ".reload_skills_trigger"


def reload_skills():
    """重新加载所有 Skill（热重载）"""
    global ALL_SKILLS, DEFAULT_SKILL_NAME, NORMAL_PAPER_CONTEXT
    # 刷新通用背景
    NORMAL_PAPER_CONTEXT = _load_normal_paper()
    if NORMAL_PAPER_CONTEXT:
        logger.info(f"[normal-paper] Reloaded {len(NORMAL_PAPER_CONTEXT)} chars")
    old_names = set(ALL_SKILLS.keys())
    ALL_SKILLS = load_all_skills()
    new_names = set(ALL_SKILLS.keys())
    # 重新确定默认角色（仅用于首次启动，热重载不影响当前活跃角色）
    if cfg.DEFAULT_SKILL_NAME_CONFIG and cfg.DEFAULT_SKILL_NAME_CONFIG in ALL_SKILLS:
        DEFAULT_SKILL_NAME = cfg.DEFAULT_SKILL_NAME_CONFIG
    else:
        DEFAULT_SKILL_NAME = next(iter(ALL_SKILLS), None) if ALL_SKILLS else None
    added = new_names - old_names
    removed = old_names - new_names
    logger.info(f"[reload] Skills reloaded: {len(ALL_SKILLS)} total, +{len(added)} -{len(removed)}, default={DEFAULT_SKILL_NAME}")
    # 清理触发文件
    RELOAD_TRIGGER.unlink(missing_ok=True)


@event_preprocessor
async def _check_reload_trigger():
    """在所有事件（包括命令）处理前检查是否需要热重载 Skill 或设置"""
    if RELOAD_TRIGGER.exists():
        reload_skills()
    if cfg.SETTINGS_RELOAD_TRIGGER.exists():
        cfg.load_runtime_config()
        cfg.apply_runtime_config()
        cfg.SETTINGS_RELOAD_TRIGGER.unlink(missing_ok=True)
        logger.info("[settings] Runtime settings reloaded from dashboard")
