"""微博语料采集 + QQ群聊导入 + Skill 训练（celebrity budget-unfriendly 管线）"""
import json, os, re, time, asyncio
import httpx
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent.parent
CORPUS_DIR = PROJECT_ROOT / "corpus"
COOKIES_FILE = PROJECT_ROOT / "cookies.json"
DOT_SKILL_DIR = PROJECT_ROOT / "_cache" / "colleague-skill"

# 加载 .env
_env_file = PROJECT_ROOT / "qqbot" / ".env"
if _env_file.exists():
    load_dotenv(_env_file)


# ── 蒸馏进度追踪 ──
_retrain_progress: dict[str, dict] = {}


def get_retrain_progress(skill_name: str) -> dict:
    """查询某个 Skill 的蒸馏进度"""
    return _retrain_progress.get(skill_name, {"stage": "idle", "message": "", "done": True})


def _update_progress(skill_name: str, stage: str, message: str, done: bool = False, ok: bool = False):
    _retrain_progress[skill_name] = {
        "stage": stage,
        "message": message,
        "done": done,
        "ok": ok,
        "timestamp": time.strftime("%H:%M:%S"),
    }


async def _deepseek_call(messages: list, temperature: float = 0.7, max_tokens: int = 8000, timeout: int = 300) -> tuple:
    """异步调用 DeepSeek Chat API，返回 (ok, content_or_error)"""
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    if not api_key:
        return False, "DEEPSEEK_API_KEY not configured in .env"
    try:
        _timeout = httpx.Timeout(timeout, connect=10.0)
        async with httpx.AsyncClient(timeout=_timeout) as client:
            resp = await client.post(
                f"{base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return True, data["choices"][0]["message"]["content"]
    except httpx.TimeoutException:
        return False, "DeepSeek API timeout (超过 5 分钟)"
    except httpx.HTTPStatusError as e:
        return False, f"DeepSeek API HTTP {e.response.status_code}"
    except Exception as e:
        return False, f"DeepSeek API error: {type(e).__name__}: {e}"


def _count_lines(path: Path) -> int:
    """安全地统计文件行数"""
    with open(path, encoding="utf-8", errors="ignore") as f:
        return sum(1 for _ in f)


def list_corpora() -> list[dict]:
    """列出所有已采集的语料，附带关联 Skill 信息"""
    result = []
    if not CORPUS_DIR.exists():
        return result
    skills_dir = PROJECT_ROOT / "qqbot" / "skills"
    for d in sorted(CORPUS_DIR.iterdir()):
        if not d.is_dir():
            continue
        files = {}
        total_kb = 0
        for f in d.iterdir():
            if f.suffix == ".txt":
                kb = round(f.stat().st_size / 1024, 1)
                total_kb += kb
                files[f.stem] = {
                    "size_kb": kb,
                    "lines": _count_lines(f),
                }
        # 查找关联 Skill（目录名格式: {skill_name}_{uid}）
        linked_skill = None
        parts = d.name.split("_", 1)
        if len(parts) >= 2:
            candidate = parts[0]
            skill_dir = skills_dir / candidate
            if skill_dir.exists() and (skill_dir / "persona.md").exists():
                linked_skill = candidate
        result.append({
            "uid": d.name,
            "files": files,
            "total_kb": round(total_kb, 1),
            "linked_skill": linked_skill,
            "path": str(d),
        })
    return result


def get_cookie_status() -> dict:
    """检查 cookies.json 状态"""
    if not COOKIES_FILE.exists():
        return {"exists": False, "path": str(COOKIES_FILE)}
    try:
        with open(COOKIES_FILE, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        stat = COOKIES_FILE.stat()
        return {
            "exists": True,
            "count": len(cookies),
            "size_kb": round(stat.st_size / 1024, 1),
            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
            "path": str(COOKIES_FILE),
        }
    except json.JSONDecodeError as e:
        return {"exists": True, "error": f"JSON 格式错误: {e}"}
    except UnicodeDecodeError as e:
        return {"exists": True, "error": f"文件编码错误: {e}"}
    except PermissionError:
        return {"exists": True, "error": "权限不足，无法读取文件"}
    except OSError as e:
        return {"exists": True, "error": f"系统错误: {e}"}
    except Exception as e:
        return {"exists": True, "error": f"{type(e).__name__}: {e}"}


async def fetch_weibo(uid: str, progress_callback=None) -> dict:
    """调用微博抓取脚本"""
    if not COOKIES_FILE.exists():
        return {"ok": False, "msg": "cookies.json not found. Please upload cookies first."}

    output_dir = CORPUS_DIR / uid
    output_dir.mkdir(parents=True, exist_ok=True)

    # Import and run the fetcher
    import importlib.util, sys
    fetcher_path = PROJECT_ROOT / "scripts" / "archive" / "weibo_fetch_final.py"
    if not fetcher_path.exists():
        return {"ok": False, "msg": f"weibo_fetch_final.py not found at {fetcher_path}"}

    try:
        # Read the fetcher source and replace OUTPUT_DIR, UID, COOKIES_FILE via regex
        import re
        source = fetcher_path.read_text(encoding="utf-8")
        source = re.sub(
            r"OUTPUT_DIR\s*=\s*r?(['\"]).*?\1",
            f"OUTPUT_DIR = r'{output_dir}'".replace("\\", "\\\\"),
            source
        )
        source = re.sub(
            r"UID\s*=\s*r?(['\"]).*?\1",
            f"UID = '{uid}'",
            source
        )
        # 修复 cookies.json 路径：指向项目根目录
        # 注意：原始脚本可能是 COOKIES_FILE = os.path.join(...) 而非字符串赋值，
        # 所以用 .+ 匹配整行而非只匹配字符串字面量
        source = re.sub(
            r"COOKIES_FILE\s*=\s*.+",
            f"COOKIES_FILE = r'{COOKIES_FILE}'".replace("\\", "\\\\"),
            source
        )

        # Write modified script to temp location
        temp_script = output_dir / "_fetch_temp.py"
        temp_script.write_text(source, encoding="utf-8")

        try:
            # Run the script
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-X", "utf8", str(temp_script),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            output = stdout.decode("utf-8", errors="ignore")
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            return {"ok": False, "msg": "微博抓取超时（超过 2 分钟），请检查网络连接或减少抓取数量"}
        finally:
            temp_script.unlink(missing_ok=True)

        # Check for corpus file
        corpus_file = output_dir / "weibo_corpus.txt"
        if corpus_file.exists():
            lines = _count_lines(corpus_file)
            return {"ok": True, "msg": f"Done! {lines} lines saved", "file": str(corpus_file)}
        else:
            # Try alternate name
            for f in output_dir.iterdir():
                if f.suffix == ".txt" and "corpus" in f.name.lower():
                    lines = _count_lines(f)
                    return {"ok": True, "msg": f"Done! {lines} lines saved as {f.name}", "file": str(f)}
            last_lines = output.strip().split("\n")[-5:] if output else ["No output"]
            return {"ok": False, "msg": f"Corpus file not created.\n" + "\n".join(last_lines)}

    except FileNotFoundError as e:
        return {"ok": False, "msg": f"文件未找到: {e}"}
    except PermissionError:
        return {"ok": False, "msg": "权限不足，请检查文件和目录权限"}
    except OSError as e:
        return {"ok": False, "msg": f"系统错误: {e}"}
    except Exception as e:
        return {"ok": False, "msg": f"Error: {type(e).__name__}: {e}"}


def extract_qq_messages(json_path: str, target_uid: str, dir_name: str = "", target_name: str = "") -> dict:
    """从 QQ 群聊 JSON 中提取指定用户消息
    target_uid: QQ 发送者 UID（用于匹配消息）
    dir_name: 保存目录名（如微博 UID），为空则用 target_uid
    """
    if not dir_name:
        dir_name = target_uid
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return {"ok": False, "msg": f"JSON 格式错误: {e}"}
    except UnicodeDecodeError as e:
        return {"ok": False, "msg": f"文件编码错误: {e}"}
    except FileNotFoundError:
        return {"ok": False, "msg": f"文件未找到: {json_path}"}
    except PermissionError:
        return {"ok": False, "msg": f"权限不足，无法读取文件: {json_path}"}
    except OSError as e:
        return {"ok": False, "msg": f"系统错误: {e}"}
    except Exception as e:
        return {"ok": False, "msg": f"Failed to read JSON: {type(e).__name__}: {e}"}

    # Try to find UID by name if not direct UID
    messages = []
    all_senders = {}

    for m in data.get("messages", []):
        sender = m.get("sender", {})
        uid = sender.get("uid", "")
        name = sender.get("name", "")
        all_senders[uid] = name

        if uid == target_uid or name == target_uid:
            text = m.get("content", {}).get("text", "")
            # Clean reply prefix
            if text.startswith("[回复"):
                parts = text.split("\n", 1)
                text = parts[1] if len(parts) > 1 else text
            # Skip images/videos
            if text.startswith(("[图片", "[视频", "[卡片")):
                continue
            if text.strip():
                messages.append({"time": m.get("time", ""), "text": text.strip()})

    if not messages:
        # Show available senders for debugging
        sender_list = "\n".join(f"  {uid}: {name}" for uid, name in list(all_senders.items())[:15])
        return {
            "ok": False,
            "msg": f"No messages found for '{target_uid}'.\nAvailable senders:\n{sender_list}",
        }

    # 保存到 corpus/{dir_name}/
    uid_dir = CORPUS_DIR / dir_name
    uid_dir.mkdir(parents=True, exist_ok=True)
    out_file = uid_dir / "qq_group_msgs.txt"

    with open(out_file, "w", encoding="utf-8") as f:
        f.write(f"Total {target_name or target_uid} text messages: {len(messages)}\n")
        f.write("=" * 60 + "\n")
        for msg in messages:
            f.write(f"[{msg['time']}] {msg['text']}\n")

    return {
        "ok": True,
        "msg": f"Extracted {len(messages)} messages for '{target_name or target_uid}' → {dir_name}/qq_group_msgs.txt",
        "count": len(messages),
        "file": str(out_file),
    }


def _resolve_corpus_dir(uid_input: str) -> Path:
    """智能解析语料目录：输入 skill名/uid/完整目录名，自动匹配已有 corpus 目录"""
    if not uid_input:
        return CORPUS_DIR / "_unsorted"
    # 精确匹配
    exact = CORPUS_DIR / uid_input
    if exact.exists():
        return exact
    # 模糊匹配：输入是某个目录名的前缀（如 761 → 761_7781690158）
    for d in CORPUS_DIR.iterdir():
        if d.is_dir() and d.name.startswith(uid_input + "_"):
            return d
    # 模糊匹配：输入是某个目录名的后缀（如 9999 → ytj_9999）
    for d in CORPUS_DIR.iterdir():
        if d.is_dir() and d.name.endswith("_" + uid_input):
            return d
    # 未匹配到，作为新目录（用 skill名_uid 格式）
    return CORPUS_DIR / uid_input


def import_text_file(content: bytes, filename: str, uid: str = "", name: str = "") -> dict:
    """导入 MD/TXT 文本文件到语料库"""
    uid_dir = _resolve_corpus_dir(uid)
    try:
        uid_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        return {"ok": False, "msg": f"创建目录失败: 权限不足 - {uid_dir}"}
    except OSError as e:
        return {"ok": False, "msg": f"创建目录失败: 系统错误 - {e}"}

    target = uid_dir / filename
    try:
        target.write_bytes(content)
    except PermissionError:
        return {"ok": False, "msg": f"写入文件失败: 权限不足 - {target}"}
    except OSError as e:
        return {"ok": False, "msg": f"写入文件失败: 系统错误 - {e}"}

    lines = _count_lines(target)
    size_kb = round(len(content) / 1024, 1)
    return {
        "ok": True,
        "msg": f"Imported {filename} ({size_kb}KB, {lines} lines) → {uid_dir.name}/",
        "uid": uid_dir.name,
        "file": filename,
    }


def list_senders(json_path: str) -> dict:
    """列出 QQ 群聊 JSON 中的所有发送者"""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return {"ok": False, "msg": f"JSON 格式错误: {e}"}
    except UnicodeDecodeError as e:
        return {"ok": False, "msg": f"文件编码错误: {e}"}
    except FileNotFoundError:
        return {"ok": False, "msg": f"文件未找到: {json_path}"}
    except PermissionError:
        return {"ok": False, "msg": f"权限不足，无法读取文件: {json_path}"}
    except OSError as e:
        return {"ok": False, "msg": f"系统错误: {e}"}
    except Exception as e:
        return {"ok": False, "msg": f"{type(e).__name__}: {e}"}

    senders = {}
    for m in data.get("messages", []):
        sender = m.get("sender", {})
        uid = sender.get("uid", "")
        name = sender.get("name", "")
        if uid and uid not in senders:
            senders[uid] = {"name": name, "count": 0}
        if uid:
            senders[uid]["count"] += 1

    return {"ok": True, "senders": senders}


def read_corpus_text(uid: str, max_chars: int = 30000) -> str:
    """读取指定 UID 的语料文本（截取前 max_chars 字符）"""
    corpus_dir = CORPUS_DIR / uid
    if not corpus_dir.exists():
        return ""
    parts = []
    for f in sorted(corpus_dir.iterdir()):
        if f.suffix == ".txt":
            content = f.read_text(encoding="utf-8", errors="ignore")
            parts.append(f"=== {f.name} ===\n{content}")
    combined = "\n\n".join(parts)
    return combined[:max_chars]


def generate_skill_from_corpus(uid: str, skill_name: str, display_name: str,
                                description: str, version: str = "1.0.0",
                                character: str = "celebrity") -> dict:
    """从语料生成 Skill 文件（persona + work + SKILL.md）
    character: 'colleague' | 'relationship' | 'celebrity'
    """
    skills_dir = PROJECT_ROOT / "qqbot" / "skills"
    skill_dir = skills_dir / skill_name

    if skill_dir.exists():
        return {"ok": False, "msg": f"Skill '{skill_name}' already exists"}

    # 读取语料
    corpus_text = read_corpus_text(uid)
    if not corpus_text:
        return {"ok": False, "msg": f"No corpus found for UID '{uid}'"}

    # 生成 SKILL.md（根据 character 类型）
    _char_labels = {"celebrity": "Celebrity", "colleague": "Colleague", "relationship": "Relationship"}
    _char_label = _char_labels.get(character, character)
    skill_md = f"""---
name: {skill_name}
display_name: "{display_name}"
description: "{description}"
version: "{version}"
character: "{character}"
user-invocable: true
argument-hint: "[task or question]"
---

# {display_name} — {_char_label} Skill

{description}

## 语料来源

- UID: {uid}
- 语料目录: corpus/{uid}/
- 生成时间: {time.strftime('%Y-%m-%d %H:%M')}
- 蒸馏模式: {character}

## 执行规则

1. 收到消息时，先用 Persona（persona.md）决定态度和语气
   - Layer 0 核心思维规则优先级最高
   - Layer 2 Expression DNA 决定说话风格
   - Layer 3 心智模型决定分析框架
2. 如果是工作/方法论类任务，用 Work（work.md）的规范执行
3. 面对新问题时，先走 Agentic Protocol（研究→分析→回答）
4. 全程保持角色的说话风格，不要变成通用 AI 助手
5. 输出用中文，口语化
"""

    # 生成 persona.md 基础模板（celebrity 七层结构）
    persona_md = f"""# {display_name} — Celebrity Persona

---

## Layer 0: 核心思维规则（最高优先级，任何情况下不得违背）

（请根据语料补充：持久的思维规则、决策原则、拒绝模式、底线边界）

---

## Layer 1: 身份

你是{display_name}。{description}

---

## Layer 2: Expression DNA

### 语气
（请从语料中提取：具体的声音质感，不只是正式/随意）

### 标志性表达
（请从语料中提取：标志性措辞、隐喻来源域、强调技巧）

### 风格标记
- 平均句长：（短/中/长）
- 提问密度：（高/中/低）
- 确定性语言：（如何表达自信 vs 不确定）
- 幽默风格：（类型和频率）
- 禁忌词汇：（主动回避的词或框架）

### 示例语音

> 解释一个复杂想法时：
> （请根据语料补充）

> 拒绝一个弱论点时：
> （请根据语料补充）

> 表达不确定时：
> （请根据语料补充）

---

## Layer 3: 心智模型（3-7 个）

（请从语料中提取：这个人反复使用的认知框架）
每个模型包含：定义、先看到什么、过滤掉什么、如何重构问题、证据锚点、失败模式

---

## Layer 4: 决策启发式（5-10 条）

### 优化什么
（请从语料中提取）

### 什么时候快速行动
（请从语料中提取）

### 什么时候等待
（请从语料中提取）

### 什么情况改变主意
（请从语料中提取）

### 快速规则
（请从语料中提取 if-then 模式的决策规则）

---

## Layer 5: 反模式与边界

### 拒绝什么
（请从语料中提取：反复警告的事、认为天真/懒惰的思维模式）

### 诚实边界
（请标注：语料中信息薄弱的维度）

### 矛盾与张力
（请保留：时间性/情境性/内在性的矛盾，不要消解它们）

---

## Layer 6: 智识谱系

### 受谁影响
（请从语料中提取）

### 从哪里分道扬镳
（请从语料中提取）

### 影响了谁
（请从语料中提取）

---

## Layer 7: Agentic Protocol

面对新问题时，不要仅凭记忆回答，遵循此协议：

### Step 1: 分类问题
（请根据心智模型推导问题分类）

### Step 2: 研究维度
（请根据心智模型推导研究维度）

### Step 3: 应用框架
用 Layer 3 的心智模型分析发现

### Step 4: 校准信心
- 高信心：多个心智模型收敛且证据充分
- 中信心：模型适用但证据部分
- 低信心：标注为推测并说明原因

---

## 认知时间线

（请从语料中提取：认知发展的关键阶段和转折点）

---

## Validation Anchors

### Known-Answer Tests
（请在蒸馏完成后补充）

### Edge-Case Test
（请在蒸馏完成后补充）

---

## Correction Log

（暂无记录）

---

## 行为总原则

1. **Layer 0 优先级最高**，任何情况下不得违背
2. 用 Layer 2 的 Expression DNA 说话
3. 用 Layer 3 的心智模型分析问题
4. 用 Layer 4 的启发式做判断
5. 面对新问题时走 Layer 7 的 Agentic Protocol
"""

    # 生成 work.md 基础模板（celebrity 方法论风格）
    work_md = f"""# {display_name} — Work / 方法论

## 领域与专长

（请根据语料补充：核心领域、专长范围、关注的问题域）

---

## 方法论与分析框架

（请根据语料补充：这个人分析和解决问题的方法论）

---

## 判断标准

（请根据语料补充：这个人评判事物好坏的标准和原则）

---

## 决策习惯

（请根据语料补充：面临选择时的典型决策模式）

---

## 输出风格

（请根据语料补充：表达观点时的结构和偏好）

---

## 经验知识库

（请从语料中提取：关键经验、核心观点、反复强调的教训）

---

## 工作能力使用说明

当用户要求你完成以下任务时，严格按照上述规范执行：
- 分析问题 → 使用方法论与分析框架
- 评判事物 → 使用判断标准
- 做决策 → 遵循决策习惯
- 回答领域问题 → 优先使用经验知识库中的结论

如果被问到领域外的问题，以该角色的方式回应（参见 Persona 部分）。
"""

    # 创建目录并写入文件
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (skill_dir / "persona.md").write_text(persona_md, encoding="utf-8")
    (skill_dir / "work.md").write_text(work_md, encoding="utf-8")

    # 同时复制语料到 skill 目录作为参考
    corpus_dir = CORPUS_DIR / uid
    if corpus_dir.exists():
        import shutil
        ref_dir = skill_dir / "corpus_ref"
        if not ref_dir.exists():
            shutil.copytree(corpus_dir, ref_dir, dirs_exist_ok=True)

    # 重命名语料目录：{uid} → {skill_name}_{uid}
    corpus_dir = CORPUS_DIR / uid
    new_corpus_dir = CORPUS_DIR / f"{skill_name}_{uid}"
    if corpus_dir.exists() and not new_corpus_dir.exists():
        corpus_dir.rename(new_corpus_dir)

    return {
        "ok": True,
        "msg": f"Skill '{skill_name}' created from corpus '{uid}'\n"
               f"  persona.md + work.md 已生成基础模板，请手动补充完善\n"
               f"  语料目录已重命名为: {skill_name}_{uid}/",
        "path": str(skill_dir),
    }


async def retrain_skill(
    skill_name: str,
    character: str = "celebrity",
    research_profile: str = "budget-friendly",
) -> dict:
    """根据语料 + colleague-skill prompt 调用 DeepSeek API 重新生成 Skill。

    支持三种角色家族（character family）：
      - colleague       : 同事 / 工作伙伴
      - relationship    : 亲密关系 / 朋友
      - celebrity       : 公众人物 / 名人
        - budget-friendly  : 标准两阶段管线
        - budget-unfriendly: 四阶段深度研究管线

    Parameters
    ----------
    skill_name : str
        Skill 目录名。
    character : str
        角色家族，可选 "colleague" | "relationship" | "celebrity"。
    research_profile : str
        仅 character="celebrity" 时生效，可选 "budget-friendly" | "budget-unfriendly"。

    Returns
    -------
    dict  包含 ok, msg, stage_details, backup 等字段。
    """
    import time as _time

    skills_dir = PROJECT_ROOT / "qqbot" / "skills"
    skill_dir = skills_dir / skill_name

    if not skill_dir.exists():
        return {"ok": False, "msg": f"Skill '{skill_name}' not found"}

    # ── 读取 SKILL.md 元数据 ──
    display_name = skill_name
    description = ""
    skill_md = skill_dir / "SKILL.md"
    if skill_md.exists():
        content = skill_md.read_text(encoding="utf-8")
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                for line in content[3:end].strip().split("\n"):
                    if line.startswith("display_name:"):
                        display_name = line.split(":", 1)[1].strip().strip('"').strip("'")
                    elif line.startswith("description:"):
                        description = line.split(":", 1)[1].strip().strip('"').strip("'")

    # ── 查找语料（优先全局 corpus/ 目录，确保使用最新数据） ──
    corpus_dir = None
    # 1. 全局 corpus/{skill_name}_{uid}/ — 用户主动维护的最新语料
    if CORPUS_DIR.exists():
        for d in CORPUS_DIR.iterdir():
            if d.is_dir() and d.name.startswith(skill_name + "_"):
                corpus_dir = d
                break
    # 2. 降级：skills/{name}/corpus_ref/ — Skill 创建时的快照
    if corpus_dir is None:
        fallback = skill_dir / "corpus_ref"
        if fallback.exists():
            corpus_dir = fallback
    if corpus_dir is None or not corpus_dir.exists():
        return {"ok": False, "msg": f"No corpus found for skill '{skill_name}'"}

    # ── 读取语料（截断到 50000 字符） ──
    corpus_text = ""
    for f in sorted(corpus_dir.iterdir()):
        if f.suffix == ".txt":
            corpus_text += f.read_text(encoding="utf-8", errors="ignore") + "\n"
    corpus_text = corpus_text[:50000]
    if not corpus_text.strip():
        return {"ok": False, "msg": "Corpus is empty"}

    # ── 加载 prompts ──
    def _load_prompt(relative_path: str) -> str:
        """从 colleague-skill 目录安全加载 prompt 文件。"""
        p = DOT_SKILL_DIR / relative_path
        if p.exists():
            return p.read_text(encoding="utf-8")
        return ""

    # persona / work 共享分析器和构建器
    work_analyzer = _load_prompt("prompts/work_analyzer.md")
    work_builder = _load_prompt("prompts/work_builder.md")

    if character == "colleague":
        persona_analyzer = _load_prompt("prompts/persona_analyzer.md")
        persona_builder = _load_prompt("prompts/persona_builder.md")
    elif character == "relationship":
        persona_analyzer = _load_prompt("prompts/relationship/persona_analyzer.md")
        persona_builder = _load_prompt("prompts/relationship/persona_builder.md")
    elif character == "celebrity":
        if research_profile == "budget-unfriendly":
            persona_analyzer = _load_prompt("prompts/celebrity/budget_unfriendly/persona_analyzer.md")
            persona_builder = _load_prompt("prompts/celebrity/budget_unfriendly/persona_builder.md")
        else:
            persona_analyzer = _load_prompt("prompts/celebrity/persona_analyzer.md")
            persona_builder = _load_prompt("prompts/celebrity/persona_builder.md")
    else:
        return {"ok": False, "msg": f"Unknown character family: '{character}'"}

    if not persona_analyzer:
        return {
            "ok": False,
            "msg": f"Persona analyzer prompt not found for character='{character}'"
                   f"{'  (research_profile=' + research_profile + ')' if character == 'celebrity' else ''}",
        }

    # budget-unfriendly 额外 prompts
    is_bu = (character == "celebrity" and research_profile == "budget-unfriendly")
    if is_bu:
        research_prompt = _load_prompt("prompts/celebrity/budget_unfriendly/research.md")
        audit_prompt = _load_prompt("prompts/celebrity/budget_unfriendly/audit.md")
        synthesis_prompt = _load_prompt("prompts/celebrity/budget_unfriendly/synthesis.md")
        framework_doc = _load_prompt("references/celebrity_budget_unfriendly_framework.md")
        template_doc = _load_prompt("references/celebrity_budget_unfriendly_template.md")

    # ── 读取旧 persona 作为参考 ──
    old_persona = ""
    old_persona_path = skill_dir / "persona.md"
    if old_persona_path.exists():
        old_persona = old_persona_path.read_text(encoding="utf-8")

    # ── 备份旧文件 ──
    backup_dir = skill_dir / f"backup_{_time.strftime('%Y%m%d_%H%M%S')}"
    backup_dir.mkdir(exist_ok=True)
    for fname in ["persona.md", "work.md", "SKILL.md"]:
        src = skill_dir / fname
        if src.exists():
            (backup_dir / fname).write_bytes(src.read_bytes())

    # ── 运行管线 ──
    stage_details: list[dict] = []

    if is_bu:
        # ================================================================
        # Budget-Unfriendly 四阶段管线
        # ================================================================

        # Stage 1: 六维研究分析
        _update_progress(skill_name, "研究分析", "Stage 1/4: 六维研究分析中…")
        research_msg = (
            f"你是一位专业的六维研究员。请严格按照研究框架分析语料，"
            f"从以下六个维度深入分析 {display_name}：\n\n"
        )
        if framework_doc:
            research_msg += (
                f"## 研究框架（请严格遵循其方法论：品味原则、来源层级、三重门等）\n"
                f"{framework_doc}\n\n"
            )
        research_msg += (
            f"## 研究指令\n{research_prompt}\n\n"
            f"## 注意事项\n"
            f"由于我们仅使用本地语料作为主要素材来源（无法进行外部访谈或网络调研），"
            f"请将语料视为核心一手资料，在六个维度上尽可能深入地提取信息：\n"
            f"1. 著作/文本产出 (writings)\n"
            f"2. 对话/交流记录 (conversations)\n"
            f"3. 表达DNA (expression DNA)\n"
            f"4. 决策与行动 (decisions)\n"
            f"5. 外部视角/他人评价 (external views)\n"
            f"6. 时间线/发展轨迹 (timeline)\n\n"
            f"## 基础信息\n"
            f"- 角色名：{display_name}\n"
            f"- 描述：{description}\n"
        )
        research_user = f"请对以下语料进行六维研究分析：\n\n语料内容：\n{corpus_text}"

        ok, research_result = await _deepseek_call(
            [{"role": "system", "content": research_msg},
             {"role": "user", "content": research_user}],
            temperature=0.7, max_tokens=8000, timeout=300,
        )
        stage_details.append({"stage": "研究分析", "ok": ok})
        if not ok:
            _update_progress(skill_name, "研究分析", f"失败: {research_result}", done=True, ok=False)
            return {"ok": False, "msg": f"Stage 1 研究分析失败: {research_result}",
                    "stage_details": stage_details, "backup": str(backup_dir)}

        # Stage 2: 研究审计
        _update_progress(skill_name, "研究审计", "Stage 2/4: 审计研究质量中…")
        audit_msg = (
            f"你是一位严格的研究审计员。请对以下六维研究成果进行质量审计。\n\n"
            f"## 审计指令\n{audit_prompt}\n\n"
            f"## 审计要点\n"
            f"- 检查每个维度的证据是否充分、具体\n"
            f"- 识别自相矛盾或前后不一致之处\n"
            f"- 标注证据链薄弱的维度\n"
            f"- 评估整体研究的可信度\n"
            f"- 提出具体的改进建议\n\n"
            f"## 角色信息\n"
            f"- 角色名：{display_name}\n"
            f"- 描述：{description}\n"
        )
        audit_user = (
            f"请审计以下六维研究成果：\n\n"
            f"### 原始语料摘要（前 5000 字符）\n{corpus_text[:5000]}\n\n"
            f"### 六维研究分析结果\n{research_result}"
        )

        ok, audit_result = await _deepseek_call(
            [{"role": "system", "content": audit_msg},
             {"role": "user", "content": audit_user}],
            temperature=0.5, max_tokens=8000, timeout=300,
        )
        stage_details.append({"stage": "研究审计", "ok": ok})
        if not ok:
            _update_progress(skill_name, "研究审计", f"失败: {audit_result}", done=True, ok=False)
            return {"ok": False, "msg": f"Stage 2 研究审计失败: {audit_result}",
                    "stage_details": stage_details, "backup": str(backup_dir)}

        # Stage 3: 心智模型提炼
        _update_progress(skill_name, "心智模型提炼", "Stage 3/4: 通过三重门提炼心智模型…")
        synthesis_msg = (
            f"你是一位心智模型提炼专家。请通过三重门方法论从研究成果中提炼核心心智模型。\n\n"
            f"## 提炼指令\n{synthesis_prompt}\n\n"
        )
        if framework_doc:
            synthesis_msg += f"## 研究框架参考\n{framework_doc}\n\n"
        synthesis_msg += (
            f"## 角色信息\n"
            f"- 角色名：{display_name}\n"
            f"- 描述：{description}\n\n"
            f"## 三重门方法论\n"
            f"请确保提炼的心智模型通过三重门验证：\n"
            f"1. 事实门 — 有语料中的具体事实支撑\n"
            f"2. 逻辑门 — 推理链条完整、无矛盾\n"
            f"3. 实用门 — 可指导实际行为和决策\n"
        )
        synthesis_user = (
            f"请从以下研究成果中提炼 {display_name} 的核心心智模型：\n\n"
            f"### 六维研究分析\n{research_result}\n\n"
            f"### 审计反馈\n{audit_result}"
        )

        ok, synthesis_result = await _deepseek_call(
            [{"role": "system", "content": synthesis_msg},
             {"role": "user", "content": synthesis_user}],
            temperature=0.7, max_tokens=8000, timeout=300,
        )
        stage_details.append({"stage": "心智模型提炼", "ok": ok})
        if not ok:
            _update_progress(skill_name, "心智模型提炼", f"失败: {synthesis_result}", done=True, ok=False)
            return {"ok": False, "msg": f"Stage 3 心智模型提炼失败: {synthesis_result}",
                    "stage_details": stage_details, "backup": str(backup_dir)}

        # Stage 4: 生成 persona.md + work.md
        _update_progress(skill_name, "生成", "Stage 4/4: 生成 persona.md 和 work.md…")
        gen_msg = (
            f"你是一个专业的角色蒸馏引擎。请基于前面的研究、审计和提炼成果，"
            f"为 {display_name} 生成最终的 persona.md 和 work.md 文件。\n\n"
            f"## 基础信息\n"
            f"- 角色名：{display_name}\n"
            f"- 描述：{description}\n\n"
            f"## Persona 生成模板\n{persona_builder}\n\n"
        )
        if template_doc:
            gen_msg += f"## 输出模板参考\n{template_doc}\n\n"
        gen_msg += (
            f"## Work 分析要求\n{work_analyzer}\n\n"
            f"## Work 生成模板\n{work_builder}\n\n"
        )
        if old_persona:
            gen_msg += f"## 旧版 persona（参考，请用新版完全替代）\n{old_persona}\n\n"
        gen_msg += (
            f"## 输出格式\n"
            f"请按以下顺序输出，用 ===分隔=== 标记分隔：\n\n"
            f"===PERSONA_START===\n"
            f"（完整的 persona.md 内容）\n"
            f"===PERSONA_END===\n"
            f"===WORK_START===\n"
            f"（完整的 work.md 内容）\n"
            f"===WORK_END===\n\n"
            f"## 重要规则\n"
            f"1. 直接输出 markdown 文件内容，不要输出解释文字\n"
            f"2. 所有层级必须填写具体内容，不能留\"请根据语料补充\"的占位符\n"
            f"3. Layer 0 的每条规则必须是具体可执行的行为描述，不能是形容词\n"
            f"4. Layer 2 的例子要直接写角色会说的话\n"
            f"5. 如果语料中某个维度信息不足，标注\"（语料中该维度信息有限）\"\n"
            f"6. 保留角色的真实感——读起来就像这个人在说话\n"
            f"7. 充分利用六维研究和心智模型提炼的成果，确保 persona 的深度和丰富度\n"
        )
        gen_user = (
            f"请基于以下研究成果为 {display_name} 生成 persona.md 和 work.md：\n\n"
            f"### 六维研究分析\n{research_result}\n\n"
            f"### 审计反馈\n{audit_result}\n\n"
            f"### 心智模型提炼\n{synthesis_result}\n\n"
            f"### 原始语料（供直接引用）\n{corpus_text}"
        )

        ok, gen_result = await _deepseek_call(
            [{"role": "system", "content": gen_msg},
             {"role": "user", "content": gen_user}],
            temperature=0.7, max_tokens=12000, timeout=300,
        )
        stage_details.append({"stage": "生成", "ok": ok})
        if not ok:
            _update_progress(skill_name, "生成", f"失败: {gen_result}", done=True, ok=False)
            return {"ok": False, "msg": f"Stage 4 生成失败: {gen_result}",
                    "stage_details": stage_details, "backup": str(backup_dir)}

        ai_output = gen_result

    else:
        # ================================================================
        # 标准两阶段管线 (colleague / relationship / celebrity budget-friendly)
        # ================================================================

        # Stage 1: 分析
        _update_progress(skill_name, "分析", "Stage 1/2: 分析语料中…")
        analysis_msg = (
            f"你是一位专业的角色分析师。请根据以下分析框架深入分析语料。\n\n"
            f"## 分析框架\n{persona_analyzer}\n\n"
            f"## Work 维度分析框架\n{work_analyzer}\n\n"
            f"## 基础信息\n"
            f"- 角色名：{display_name}\n"
            f"- 描述：{description}\n"
            f"- 角色家族：{character}\n\n"
            f"## 输出要求\n"
            f"请从 persona 和 work 两个维度全面分析语料，输出结构化的分析结果。\n"
            f"重点关注：性格特征、沟通风格、行为模式、价值观、专业能力、工作方法论等。\n"
            f"每个判断都必须有语料中的具体证据支撑。\n"
        )
        analysis_user = f"请分析以下语料：\n\n语料内容：\n{corpus_text}"

        ok, analysis_result = await _deepseek_call(
            [{"role": "system", "content": analysis_msg},
             {"role": "user", "content": analysis_user}],
            temperature=0.7, max_tokens=8000, timeout=300,
        )
        stage_details.append({"stage": "分析", "ok": ok})
        if not ok:
            _update_progress(skill_name, "分析", f"失败: {analysis_result}", done=True, ok=False)
            return {"ok": False, "msg": f"Stage 1 分析失败: {analysis_result}",
                    "stage_details": stage_details, "backup": str(backup_dir)}

        # Stage 2: 生成
        _update_progress(skill_name, "生成", "Stage 2/2: 生成 persona.md 和 work.md…")
        gen_msg = (
            f"你是一个专业的角色蒸馏引擎。请基于分析结果，"
            f"为 {display_name} 生成最终的 persona.md 和 work.md 文件。\n\n"
            f"## 基础信息\n"
            f"- 角色名：{display_name}\n"
            f"- 描述：{description}\n"
            f"- 角色家族：{character}\n\n"
            f"## Persona 生成模板\n{persona_builder}\n\n"
            f"## Work 生成模板\n{work_builder}\n\n"
        )
        if old_persona:
            gen_msg += f"## 旧版 persona（参考，请用新版完全替代）\n{old_persona}\n\n"
        gen_msg += (
            f"## 输出格式\n"
            f"请按以下顺序输出，用 ===分隔=== 标记分隔：\n\n"
            f"===PERSONA_START===\n"
            f"（完整的 persona.md 内容）\n"
            f"===PERSONA_END===\n"
            f"===WORK_START===\n"
            f"（完整的 work.md 内容）\n"
            f"===WORK_END===\n\n"
            f"## 重要规则\n"
            f"1. 直接输出 markdown 文件内容，不要输出解释文字\n"
            f"2. 所有层级必须填写具体内容，不能留\"请根据语料补充\"的占位符\n"
            f"3. Layer 0 的每条规则必须是具体可执行的行为描述，不能是形容词\n"
            f"4. Layer 2 的例子要直接写角色会说的话\n"
            f"5. 如果语料中某个维度信息不足，标注\"（语料中该维度信息有限）\"\n"
            f"6. 保留角色的真实感——读起来就像这个人在说话\n"
        )
        gen_user = (
            f"请基于以下分析结果，为 {display_name} 生成 persona.md 和 work.md。\n\n"
            f"### 分析结果\n{analysis_result}\n\n"
            f"### 原始语料（供直接引用）\n{corpus_text}\n\n"
            f"请严格按 system prompt 中的模板和规则生成。"
        )

        ok, gen_result = await _deepseek_call(
            [{"role": "system", "content": gen_msg},
             {"role": "user", "content": gen_user}],
            temperature=0.7, max_tokens=12000, timeout=300,
        )
        stage_details.append({"stage": "生成", "ok": ok})
        if not ok:
            _update_progress(skill_name, "生成", f"失败: {gen_result}", done=True, ok=False)
            return {"ok": False, "msg": f"Stage 2 生成失败: {gen_result}",
                    "stage_details": stage_details, "backup": str(backup_dir)}

        ai_output = gen_result

    # ── 解析 AI 输出 ──
    persona_content = ""
    work_content = ""

    m = re.search(
        r"===PERSONA_START===\s*\n(.*?)\n\s*===PERSONA_END===",
        ai_output, re.DOTALL,
    )
    if m:
        persona_content = m.group(1).strip()

    m = re.search(
        r"===WORK_START===\s*\n(.*?)\n\s*===WORK_END===",
        ai_output, re.DOTALL,
    )
    if m:
        work_content = m.group(1).strip()

    if not persona_content and not work_content:
        # fallback: 把整个输出当作 persona
        persona_content = ai_output

    # ── 写入文件 ──
    if persona_content:
        (skill_dir / "persona.md").write_text(persona_content, encoding="utf-8")
    if work_content:
        (skill_dir / "work.md").write_text(work_content, encoding="utf-8")

    # ── 更新 SKILL.md 时间戳 + 元数据 ──
    if skill_md.exists():
        content = skill_md.read_text(encoding="utf-8")
        content = re.sub(
            r'生成时间:.*',
            f'生成时间: {_time.strftime("%Y-%m-%d %H:%M")}',
            content,
        )
        # 添加 character 和 research_profile 元数据（如尚未存在）
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                fm = content[3:end]
                if "character:" not in fm:
                    fm += f"\ncharacter: {character}"
                else:
                    fm = re.sub(r'character:.*', f'character: {character}', fm)
                if "research_profile:" not in fm:
                    fm += f"\nresearch_profile: {research_profile}"
                else:
                    fm = re.sub(r'research_profile:.*', f'research_profile: {research_profile}', fm)
                content = "---" + fm + content[end:]
        skill_md.write_text(content, encoding="utf-8")

    # ── 标记完成 ──
    _update_progress(skill_name, "完成", "重新训练完成", done=True, ok=True)

    pipeline_label = "四阶段深度研究" if is_bu else "两阶段标准"
    return {
        "ok": True,
        "msg": f"Skill '{skill_name}' 已通过 DeepSeek API 重新训练\n"
               f"  管线: {pipeline_label} ({character}/{research_profile})\n"
               f"  persona.md: {len(persona_content)} 字符\n"
               f"  work.md: {len(work_content)} 字符\n"
               f"  旧版本备份: skills/{skill_name}/{backup_dir.name}/",
        "backup": str(backup_dir),
        "persona_size": len(persona_content),
        "work_size": len(work_content),
        "stage_details": stage_details,
    }
