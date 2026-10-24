# normal-paper（通用共享背景知识）

这个目录放**所有角色共享的领域背景资料**：圈内术语、礼仪规则、共同设定的文化背景等。不需要写进每个角色人设的内容放这里。

## 注入规则

- 只有 `basic/*.md` 会**全量注入**到每个角色的 system prompt（`skill_manager._load_normal_paper()`）；
- `basic/` 之外的文件**不进 prompt**，只在角色调用 `search_corpus` 工具时被检索到。

## 公开仓库只保留示例

真实内容因涉及特定圈子/真人，只保留在作者本地，不进入公开仓库。本仓库只提交：

```
normal-paper/
├── README.md                    # 本文件
└── examples/
    └── basic-rules.example.md   # 示例：basic/ 下文件的写法
```

想启用共享背景：在本地创建 `normal-paper/basic/`，把 `examples/basic-rules.example.md` 复制过去按需改写即可（`basic/*.md` 支持热重载，随角色技能一起刷新）。
