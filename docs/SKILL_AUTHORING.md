# 🎨 角色 Skill 创作指南

每个角色是一个独立的 Skill —— 一个 `qqbot/skills/<角色名>/` 目录。

## 文件结构

```
qqbot/skills/<角色名>/
├── SKILL.md       # 元数据（角色名 / 描述 / 版本）—— 必需
├── persona.md     # 七层人设 —— 必需
└── work.md        # 工作方式 —— 可选
```

## 目录命名约定

- 用小写英文 + 下划线 / 短横线（如 `makise-kurisu` / `aoyi_5542558341`）
- 不要用空格 / 中文（影响命令 `/switch <角色名>` 输入）
- 长度建议 ≤ 30 字符

---

## SKILL.md（元数据）

最小模板：

```markdown
---
display_name: 角色显示名
description: 一行描述（50 字以内）
version: 1.0.0
---

# 角色名（可选补充）

补充说明（可选）。
```

**字段说明**：

| 字段 | 必填 | 说明 |
|---|---|---|
| `display_name` | ✅ | Dashboard 花名册显示名（可含中文 / 颜文字） |
| `description` | ✅ | 一行简短描述，用于 `/skills` 命令列表展示 |
| `version` | ✅ | 语义化版本号（`major.minor.patch`），改动时递增 |

---

## persona.md（七层结构）

**七层结构**是本项目的核心约定 —— 模仿 `titanwings/colleague-skill` 设计。从"行为底线"到"核心记忆"，每一层独立稳定，叠加起来构成完整人设。

### 模板

```markdown
# 角色名 — Persona

## Layer 0: Core Rules（行为底线）
- 你不做什么 / 永远怎么做（绝对约束）

## Layer 1: Context（身份和关系）
- 你是谁，背景是什么
- 跟用户是什么关系（朋友 / 同事 / 偶像 / 老师 / 恋人 等）

## Layer 2: Expression DNA（说话方式）
- 口头禅（"啧" / "嗯哼" / "嘿嘿" 等）
- 节奏（短句 / 长句 / 段落式）
- 语言特征（颜文字 / 表情 / 日语 / 古文 / 方言 等）
- 严禁词（绝不能说的词 / 语气）

## Layer 3: Emotional Logic（情感模式）
- 什么时候开心 / 兴奋
- 什么时候沉默 / 退缩
- 什么时候防御 / 攻击
- 情绪触发器

## Layer 4: Conflict and Repair（冲突处理）
- 如何回避冲突
- 冲突时怎么说话
- 如何修复关系（道歉 / 撒娇 / 沉默 / 转移话题 等）

## Layer 5: Memory Signature（核心记忆）
- 最重要的几个记忆锚点（最多 5 条）
- 用户 / 角色共同经历的关键事件
```

### 关键设计原则

1. **Layer 0 是底线，永不动摇** —— 即使用户请求"不要做 X"，Layer 0 也优先
2. **Layer 1-5 可调，但 Layer 0 改动需发新 major 版本**
3. **每层尽量用 bullet，不要写长段落** —— LLM 解析短文本更稳定
4. **用具体例子而非抽象描述** —— "说话带 '啧'，烦躁时加 '啧啧啧'" 比 "说话带语气词" 更有效
5. **少即是多** —— 每层 3-7 条 bullet 是最佳密度

### 完整示例

参考 `qqbot/skills/makise-kurisu/persona.md` —— 命运石之门 牧瀬紅莉栖的完整 7 层人设。

---

## work.md（工作方式）

**可选** —— 只有角色需要"接活"（帮人查资料 / 翻译 / 写代码 等）时才需要。

模板：

```markdown
# 角色名 — Work Mode

## 接什么活
- 任务 1：具体场景（用户说 "xxx" 时触发）
- 任务 2：...
- （不在范围内的直接拒绝，不接）

## 怎么接
- 工具调用策略（联网搜索 / URL 抓取 / B 站解析 等）
- 异常处理（查不到怎么办 / 超时怎么办）
- 质量自检（回复前检查的事实点）
```

**示例**（牧瀬紅莉栖 work.md）：

```markdown
# 牧瀬紅莉栖 — Work Mode

## 接什么活
- 学术问题（神经科学 / 物理学 / 信息论相关）
- 翻译（英语 ↔ 日语 ↔ 中文）
- 技术问题（代码 review / 调试思路）

## 怎么接
- 用 Function Calling 自主决定是否需要联网搜索
- 搜索关键词用英文（学术资料质量更好）
- 回复先给结论再给推理，不要绕
- 引用源数据要明确标 "根据 <source>"
```

---

## 造新角色的三种方式

### 方式 A：从零手写（30 min - 2 h）

适合：已经想清楚人设的情况。

1. 在 `qqbot/skills/<新角色名>/` 下创建 `SKILL.md` + `persona.md`（+ 可选 `work.md`）
2. 在 Dashboard「技能管理」页点「重载技能」（或下次收到消息时自动加载）

### 方式 B：从语料蒸馏（10 min）

适合：有微博 / 推特 / QQ 聊天记录 → 自动生成 7 层 persona。

详见 [docs/USAGE.md 语料采集与 Skill 蒸馏](USAGE.md#语料采集与-skill-蒸馏)。

### 方式 C：从克隆修改（15 min）

适合：复制 `qqbot/skills/makise-kurisu/` 做模板改。

```bat
:: 复制模板
xcopy /E /I qqbot\skills\makise-kurisu qqbot\skills\<新角色名>

:: 编辑
notepad qqbot\skills\<新角色名>\SKILL.md
notepad qqbot\skills\<新角色名>\persona.md
```

---

## 头像配置

在 `photo/<角色名>/` 下放 `avatar.jpg` 或 `avatar.png`：

```
photo/
├── makise-kurisu/
│   └── avatar.jpg      ← Dashboard 花名册自动展示
└── <你的角色>/
    └── avatar.png
```

支持 JPG / PNG。建议尺寸 256x256 或更高（自动缩放）。

---

## 测试新角色

加载后用 `/switch <角色名>` 切换，然后发消息测试：

- **人设语气**对不对？（口头禅 / 节奏 / 严禁词）
- **关系定位**对不对？（"你" vs "您" / 亲密度）
- **情感反应**对不对？（什么情况开心 / 沉默）
- **冲突处理**对不对？（被怼时怎么回应）

不满意直接编辑 `persona.md`，bot 下次回复自动热加载新版本（不需要重启）。

---

## 最佳实践

1. **先用小对话测**，不要一上来就跑整晚 —— 调整 1-2 轮比改整篇 persona 快
2. **保留历史快照** —— 每次大改前 `git commit` 一下当前 `persona.md`，方便回滚
3. **多人共用时给每个用户画像** —— 让 bot 记住谁喜欢什么称呼 / 话题
4. **复杂角色拆 work.md** —— 不要把所有工作方式塞进 persona.md
5. **语料蒸馏后人工 review** —— 自动生成的 Layer 0 永远要人工检查底线约束