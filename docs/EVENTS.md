# 活动检索（search_events）

> 2026-11-10 接入。解决「午前4時这周有什么活动」这类问题：只拿一条活动汇总微博会漏日期/把别的团当成她。

## 数据源

1. **花音街**（huayinjie.com）
   - 团体页「即将出演」：日期、活动名、场馆、发布状态
   - 活动页：开场/演出时间、场地、票价
2. **秀动**（showstart.com）
   - `site:showstart.com <团体>` 的活动页/摘要
3. **官方补搜**
   - DeepSeek Search 查 `<团体> 演出 活动 <年月>`，覆盖官微、B站、政府许可等

## 工具

```json
{
  "name": "search_events",
  "arguments": {
    "entity": "午前4時",
    "city": "广州",
    "days": 7
  }
}
```

- `days` 默认 7，最多 30；只保留未来 N 天内、日期明确的活动
- 结果带日期/时间/活动名/场馆/状态/来源链接
- 5 分钟缓存，避免同一轮重复抓
- 只挂给 `event_search: true` 的角色

## 角色分类（当前 16 个）

| 类别 | 角色 | search_events |
|---|---|---|
| 偶像本人/团内成员 | 761、iteru、aoyi、koimo、mika、momo、nana、rose、ytj | ✅ |
| OTA/现场粉丝/关系者 | guuo、jinmao、skingfd、zyw | ✅ |
| 二次元角色 | amadeus-kurisu、makise-kurisu、yukina | ❌ |

给新角色开启：在 `qqbot/characters/<name>/SKILL.md` frontmatter 加：

```yaml
event_search: true
```

## 实测

`search_events("午前4時", city="广州", days=7)`：

```
2026/11/11 19:30 | 「花束(はなたば)」主题定期公演第⑦期 Final | MAO LiveHouse广州中大店（二号馆） | 已发布
2026/11/15 11:00 | 光棱Fes1.0 · 异世界魔女降临 / 张幼柠生日SP | 音乐唐人馆（待收录） | 已发布
```

不会再出现「15 号是别的团」这种推断；11/14 的恋音契约 × 碎夜瑠璃也不会被算成午前4時。

> 秀动当前本周没有命中未来场次时，工具会显示花音街明确活动；秀动有结果时会追加「秀动/网页线索」。
