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
| momo | 兔月桃桃 | 花音街未找到对应成员页 | ❌ 保留旧图 |
| rose | 罗姐 | 花音街未找到对应成员页 | ❌ 保留旧图 |

来源：`https://www.huayinjie.com/idols/<成员页 ID>`

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
