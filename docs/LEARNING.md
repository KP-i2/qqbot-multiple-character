# QQ 群只读学习（P1 学习线）

> 2026-11-10 起，测试角色：`iteru`（小秋）；群：`473856739`、`232079122`。

## 做什么

`learning.py` 在**所有消息处理之前**（priority=1，`block=False`）只读采集白名单群的发言：

- 只采集 `learning_enabled: true` 且群号在 `learning_groups` 里的角色
- 不回复、不 @、不调用模型、不把群消息发到别处
- 原始消息存本地 `learning_messages`，最多 7 天；摘要进 `memory_events`，scope=`group_<gid>`（群共享）
- 每 10 分钟扫描一次；攒够 `learning_min_messages` 且距上次摘要超过 `learning_summary_interval_minutes` 才摘要

摘要只提取群体级信息（话题/梗/活动/称呼偏好），**明确禁止个人隐私、身份、联系方式、住址、工作/健康/情感信息**；模型输出必须带群里逐字引用，引不到就丢弃。

## iteru 配置

`qqbot/characters/iteru/SKILL.md` frontmatter：

```yaml
memory_enabled: true
memory_extract: true
learning_enabled: true
learning_sources: qq
learning_groups: 473856739,232079122
learning_min_messages: 8
learning_summary_interval_minutes: 60
```

给其他角色开启：复制这几行，改 `learning_groups`；没有 `learning_enabled` 的角色完全不会被采集。

## 记忆读取

- 群聊：`iteru` 回复时同时检索「群共享记忆（group_<gid>）+ 该用户记忆（group_<gid>_<uid>）」
- 私聊：只检索该用户记忆
- 其他角色读不到 `iteru` 的群记忆（`skill` 列隔离）

## 日志

`qqbot/logs/chat.log`：

```
[LEARN] 采集 iteru group=... user=...
[LEARN] 摘要 raw=312 items=3 skill=iteru group=...
[LEARN] 摘要入库 3 条 | skill=iteru group=...
```

## 学习报告

Dashboard →「记忆」→「学习报告（近 7 天）」：

- 摘要次数、覆盖消息数
- accepted / rejected（quote 校验没过或格式无效）
- 最近入库的群体记忆

API：`GET /api/learning/report?days=7&skill=iteru`

每次摘要都会写一条 `learning_runs`，失败也会记录原因（无返回/冷却），不会再“只看日志猜”。

## B 站

已安装并锁进 `requirements.txt`：`bilibili-api-python>=17.0.0`（实测 17.4.2 可搜索视频）。

**现有 B 站语料/蒸馏行为不变**，本次没有给任何角色开 B 站学习源；以后要接，只需在 `learning.py` 加 `BilibiliSource`，并在角色上写 `learning_sources: qq,bilibili`。
