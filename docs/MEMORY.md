# 长期记忆（Life Memory）· P0

> 2026-11-10 起，先给 `amadeus-kurisu` 灰度；其他角色默认关闭。

## 它做什么

每轮对话结束后，后台用一个便宜模型从「用户这句话 + 角色回复」里抽取 0~5 条值得长期记住的事：

- `fact` 事实、`preference` 偏好、`promise` 约定
- `relationship` 关系、`interest` 兴趣、`boundary` 边界
- `event` 事件

每条必须带 **原文引用**（`source_quote`）；引用在原文里找不到就丢弃，防止模型自己编记忆。

下次对话前，按「关键词 + 重要度 + 新近度」取最多 8 条注入 system prompt；没有相关的就只取少量最近/重要背景，不硬塞。

## 隔离规则

| 维度 | 规则 |
|---|---|
| 角色 | `skill` 列隔离，A 角色的记忆不会给 B 角色 |
| 私聊 | `scope = user_<uid>` |
| 群聊 | `scope = group_<gid>_<uid>`（群里每个人与角色的对话各自独立） |
| 渠道 | 私聊/群聊作用域不互通 |

`/memory`、`/forget` 只能在当前会话作用域内操作，且仅管理员。

## 文件与开关

- 数据库：`qqbot/data/memory.db`（SQLite WAL，已被 `.gitignore` 忽略）
- 可用环境变量 `MEMORY_DB_PATH` 覆盖（测试用）
- 角色开关（`SKILL.md` frontmatter）：
  ```yaml
  memory_enabled: true    # 启用检索 + 抽取
  memory_extract: true    # 只读角色可设 false
  ```
- 当前只有 `amadeus-kurisu` 打开；其他角色仍是纯会话历史 + 用户画像。

## 命令

```text
/memory           查看当前角色、当前会话最近 20 条记忆
/forget <id>      删除指定一条（软删除）
/forget all       清空当前作用域
/forget 关键词     删除匹配关键词的记忆
```

> 仅管理员可用；普通用户看不到也删不了。

## 限制（P1/P2 继续）

- 检索是关键词 + 规则打分，没有 embedding / 向量召回。
- 没有记忆衰减/冲突合并；同一件事说两遍可能存两条。
- 没有 Dashboard 记忆页；目前只能 `/memory` 看、`/forget` 删。
- 主动回访（P1）和成本账本/备份（P2）还没接。
- 抽取每轮一次，amadeus 会产生一笔很小的额外模型调用；并发上限 2，满了跳过。

## 验证

```powershell
python -m py_compile qqbot\plugins\char_chat\memory.py
# 手工：启动 bot 后切到 amadeus
#   /memory              应为空
#   聊两句个人信息
#   /memory              应出现带日期的记忆
#   /forget <id>         再查应消失
```
