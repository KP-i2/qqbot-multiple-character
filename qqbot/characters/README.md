# 角色目录

公开仓库只带两个 anime 示例角色（原作《命运石之门》系）：

| 目录 | 说明 |
|---|---|
| `makise-kurisu/` | 牧瀬红莉栖（含 7 层人设、工作方式、原作摘录语料快照） |
| `amadeus-kurisu/` | Amadeus 红莉栖（同一角色的 AI 版本，人设与语料独立） |

其余角色（真人系 / 其他二次元系）都在作者本地维护，**不进入公开仓库**——见根目录 `.gitignore` 的 `qqbot/characters/*` 规则。

## 角色目录结构

```
qqbot/characters/<角色名>/
├── SKILL.md       # 元数据（display_name / description / version）+ 可选控制层正文
├── persona.md     # 七层人设（核心规则 → 关系 → 表达 DNA → 情感 → 冲突 → 记忆）
├── work.md        # 工作方式（可选）
├── modes/         # 模式库（可选，配合 include_subdirs 注入）
└── corpus_ref/    # 造角色时的语料快照（部分角色有）
```

## 关键约定

- **角色 key 永远是目录名**：`/switch` 后面写目录名（不是 front matter 里的 `name`）。
- `persona.md` / `work.md` 至少存在一个，否则加载器会跳过该目录。
- `SKILL.md` front matter 支持的可选字段（由 `plugins/char_chat/skill_manager.py` 消费）：
  `include_skill_body`、`include_subdirs`、`allowed_users`、`include_normal_paper`、`exclude_files`、
  `initiate_probability`、`initiate_hint`、`emoji_images`。
- 新增角色最快的方式：复制任一示例目录，改 `SKILL.md` + `persona.md`（详见 `docs/SKILL_AUTHORING.md`）。
