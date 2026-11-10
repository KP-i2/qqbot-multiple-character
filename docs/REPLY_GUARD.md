# 回复输出校验（REPLY_GUARD）

> 2026-11-10 新增。目标：坏回复不发给用户；已经发出去的，尽量撤回并只重写一次。

## 检查什么

`reply_guard.validate_reply()`：

- 工具调用/系统标记：`<system>`、`<parameter>`、`<function>`、`<tool_call(s)>`、DSML
- 明确的错误兜底：`⚠️ 响应解析失败`、`⚠️ LLM 调用失败`
- 连续问号乱码：`????`、`�` 替换字符
- 空内容或只有标点

## 流程

```
生成 reply
  ↓
发送前校验
  ├─ 通过 → 发送
  └─ 失败 → 重写一次
        ├─ 通过 → 发送
        └─ 仍失败 → 发安全兜底（不发原文）
  ↓
发送后二次校验
  ├─ 通过 → 正常结束
  └─ 失败 → 撤回已发消息（所有分段）
        ├─ 还没重试过 → 重写一次 → 通过则补发
        └─ 已重试过 / 拿不到 message_id → 不再重试
```

“只重试一次”由 `_reply_retry_used` 控制，发送前和发送后共用，不会循环。

## 日志

```
[REPLY_GUARD] 发送前校验失败 reason=tool_markup ...
[REPLY_GUARD] 重写通过，使用重写内容
[REPLY_GUARD] 发送后校验失败 reason=garbled ids=[...]
[REPLY_GUARD] 已请求撤回 message_id=...
[REPLY_GUARD] 撤回后重写通过，已补发
```

## 限制

- QQ 普通消息撤回有时限；发送后立即撤回通常可以，超过时限 `delete_msg` 可能返回成功但实际不生效
- 如果 OneBot 发送响应没有 `message_id`，无法撤回，只能记录告警
- 发送前的校验已经能拦住大部分模型输出错误；发送后校验主要防漏网
