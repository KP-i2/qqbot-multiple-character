# 主动消息（P1）· amadeus-kurisu / iteru

> 2026-11-10 起接入；用户确认参数：每天最多 20 条、静默 02:00–08:00、允许「随机想你」。
> 2026-11-10 调整：**每天最多 2 条**、最小间隔 8 小时；优先接最近 12 小时对话或记忆，没有依据就不发。

## 规则

| 项 | 值 |
|---|---|
| 渠道 | **只走私聊**，不发群 |
| 目标 | 角色 `allowed_users`；为空时只发 `ADMIN_QQ`（避免给陌生人发私信） |
| 每日上限 | `proactive_daily_limit: 2`（每个用户单独计数） |
| 静默 | `proactive_quiet_hours: "2-8"`，即 02:00–07:59 |
| 最小间隔 | `proactive_min_gap_minutes: 480`，即 8 小时 |
| 打扰保护 | 对方最近 30 分钟说过话 → 跳过 |
| 连发保护 | 最后一条是角色自己、对方还没回 → 跳过，不追发 |
| 话题依据 | ①未用过的 `promise` / `interest` / `relationship` 记忆；②最近 12 小时的真实对话；两者都没有 → **不发** |
| 时间感 | prompt 注入当前日期/时间，禁止中午说“睡了吗”、深夜问“在干嘛” |
| 回执 | 每次成功/失败写 `proactive_log`（理由、记忆 id、文本、message_id、错误） |

扫描频率：启动后等 60s，之后每 10 分钟一次；**只让当前活跃角色主动**，每个角色每轮最多发 1 条。

发出去的主动消息会写进该角色的私聊历史（`user_<uid>_<skill>`），这样用户回复时模型知道刚说过什么。

> 2026-11-10 修复：之前允许多个角色同时主动，但回复永远由 `global_active_skill` 接，导致“amadeus 发、iteru 接”。现在只在当前角色上主动；随机搭话也禁止编“你上次说的实验/那件事”。

## 开关（`SKILL.md` frontmatter）

```yaml
proactive_enabled: true
proactive_daily_limit: 2
proactive_quiet_hours: "2-8"
proactive_min_gap_minutes: 480
proactive_random: true
proactive_random_chance: 0.05
```

想关掉：把 `proactive_enabled` 改为 `false`，重载技能或重启。

## 日志

`qqbot/logs/chat.log`：

```
[PROACTIVE] 已发送 | skill=amadeus-kurisu user=... reason=... memory_id=... text='...'
[PROACTIVE] 发送失败 ...
```

## 注意

- **2 条/天是硬上限**，实际还会被 8 小时间隔、30 分钟打扰保护、静默时段和概率压低，可能一天只发 0–2 条。
- 没有记忆、也没有最近 12 小时对话时，**宁可不发**，不再硬发“在吗/突然想到”。
- 对方还没回上一条时不会追发；所以一天最多 2 条，实际可能是 0–2 条。
- 主动消息会真实消耗模型 token；失败不会重试轰炸。
- 目前 `amadeus-kurisu`、`iteru` 打开；其他角色需要同时开 `memory_enabled` 才会用记忆做理由。
