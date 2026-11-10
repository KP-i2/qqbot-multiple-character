# 偶像头像来源（花音街，2026-11-10）

从花音街成员页的 hero 图取 1280px，竖图裁上半部方形、缩到 1024×1024，命名：

```
photo/<角色>/avatar_huayinjie_20261110.jpg
```

旧头像全部保留，不覆盖。

| 角色 | 花音街成员 | 成员页 ID | 新头像 |
|---|---|---|---|
| 761 | 小船CHIYORI | `cmtka0y7l000j01pfjwk4c9os` | ✅ |
| mika | 小未Mika | `cmtka10l1000y01pf9ry5o43a` | ✅ |
| koimo | 小芋Koimo | `cmtk7olc3000o01pf0yrzm61p` | ✅ |
| nana | 奈奈Nana | `cmtk7oj91000901pf9zteivw7` | ✅ |
| ytj | 羽悠Yuu | `cmtk7ojxv000e01pfskkat2ny` | ✅ |
| iteru | 小凍Iteru | `cmtl4bnm106q701pfrg0kqmbd` | ✅ |
| aoyi | Aoyi | `cmtk7pqrg004201pfbigkughp` | ✅ |
| momo | 兎月もも（君に、胸キュン。） | 花音街无；用 **IDOL Watch 官方档案图**（399×399） | ✅ `avatar_idolwatch_20261110.jpg` |
| rose | 神代ロゼ（ジエメイ） | 花音街无；用 **X/Twitter 官方头像 CDN**（400×400） | ✅ `avatar_twitter_20261110.jpg` |

来源：`https://www.huayinjie.com/idols/<成员页 ID>`

## momo / rose 的其他渠道

- **X 不登录**：直接打开 `x.com/<handle>` 会返回登录墙，页面正文读不到
- **Twitter 头像 CDN**：`pbs.twimg.com/profile_images/...` 不需要登录；但 momo 语料里的旧 URL 已 404
- **momo**：改用 [IDOL Watch 官方档案](https://idolwatch.jp/member/7984) 的 `profile_kimikyun_momo.jpg`（官方渠道，399×399）
- **rose**：`@jiemei_rose` 的 400×400 头像 CDN 可用，已保存 `avatar_twitter_20261110.jpg`
- 也试过 `unavatar.io/x/<handle>`（第三方头像代理），momo 拿到的是另一张官方图；最终 momo 用 IDOL Watch 原始档案图

momo 的 X 账号：`@kimikyun_momo`；rose：`@jiemei_rose`。旧图都保留。

## 配置

对应角色 SKILL.md：

```yaml
avatar_file: avatar_huayinjie_20261110.jpg
```

iteru 的 `photo/iteru/selected.txt` 也指向新图。切换用：

```
/avatar
/avatar avatar_huayinjie_20261110.jpg
/avatar avatar.png
```

选择顺序：`selected.txt` > `avatar_file` > 目录扫描；旧图不删除。
