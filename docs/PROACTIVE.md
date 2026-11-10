# 主动消息（P1）· amadeus-kurisu

> 2026-11-10 起接入；用户确认参数：每天最多 20 条、静默 02:00–08:00、允许「随机想你」。

## 规则

| 项 | 值 |
|---|---|
| 渠道 | **只走私聊**，不发群 |
| 目标 | 角色 `allowed_users`；为空时只发 `ADMIN_QQ`（避免给陌生人发私信） |
| 每日上限 | `proactive_daily_limit`，默认 20（每个用户单独计数） |
| 静默 | `proactive_quiet_hours: "2-8"`，即 02:00–07:59 |
| 最小间隔 | `proactive_min_gap_minutes`，默认 25 分钟 |
| 打扰保护 | 对方最近 30 分钟说过话 → 跳过 |
| 理由 | 优先用记忆里的 `promise` / `interest` / `relationship`；没有时可按 `proactive_random_chance` 随机「想你」 |
| 回执 | 每次成功/失败写 `proactive_log`（理由、记忆 id、文本、message_id、错误） |

扫描频率：启动后等 60s，之后每 10 分钟一次；每个角色每轮最多发 1 条。

## 开关（`SKILL.md` frontmatter）

```yaml
proactive_enabled: true
proactive_daily_limit: 20
proactive_quiet_hours: "2-8"
proactive_min_gap_minutes: 25
proactive_random: true
proactive_random_chance: 0.08
```

想关掉：把 `proactive_enabled` 改为 `false`，重载技能或重启。

## 日志

`qqbot/logs/chat.log`：

```
[PROACTIVE] 已发送 | skill=amadeus-kurisu user=... reason=... memory_id=... text='...'
[PROACTIVE] 发送失败 ...
```

## 注意

- 20 条/天是**硬上限**，实际还会被 25 分钟间隔、30 分钟打扰保护、静默时段和随机概率压低，通常远低于 20。
- 主动消息会真实消耗模型 token；失败不会重试轰炸。
- 目前只给 `amadeus-kurisu` 打开；其他角色需要同时开 `memory_enabled` 才会用记忆做理由。
