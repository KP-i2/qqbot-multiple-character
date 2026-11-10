# normal-paper（通用共享背景知识）

这个目录放**所有角色共享的领域背景资料**：圈内术语、礼仪规则、共同设定的文化背景等。不需要写进每个角色人设的内容放这里。

## 注入规则

- `basic/` 下的 `.md`（**含子目录，递归**）默认会**全量注入**到每个角色的 system prompt（`skill_manager._load_normal_paper()`）；
- **例外**：`basic/humanlike.md` 是通用说话风格，单独加载（`include_humanlike`，默认开）；`include_normal_paper` 只控制领域背景（现场礼仪/人设补充），不会把说话风格一起关掉；
- 二次元角色建议在 `SKILL.md` 写 `include_normal_paper: false`，避免地偶圈礼仪稀释人设；需要保留说话风格就不要关 `include_humanlike`；
- 顶层文件用文件名做标题（如「规则」），子目录文件用相对路径（如「子目录/规则」），避免重名；
- 隐藏目录（`.` 开头）和 `__pycache__` 会跳过；不要在 `basic/` 下放备份目录，否则备份 md 也会被注入；
- `basic/` 之外的文件**不进 prompt**，只在角色调用 `search_corpus` 工具时被检索到。

## 公开仓库只保留示例

真实内容因涉及特定圈子/真人，只保留在作者本地，不进入公开仓库。本仓库只提交：

```
normal-paper/
├── README.md                    # 本文件
└── examples/
    └── basic-rules.example.md   # 示例：basic/ 下文件的写法
```

想启用共享背景：在本地创建 `normal-paper/basic/`，把 `examples/basic-rules.example.md` 复制过去按需改写即可（`basic/**/*.md` 支持热重载，随角色技能一起刷新）。
