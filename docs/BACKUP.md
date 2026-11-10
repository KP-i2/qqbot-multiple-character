# 记忆/对话数据备份（P2）

## 备份

```powershell
python scripts\backup_memory.py
# 或双击 scripts\backup_memory.bat
```

默认输出 `backups/qqbot_data_YYYYmmdd_HHMMSS.zip`（已被 `.gitignore` 忽略），包含：

- `memory.db` —— 用 SQLite backup API 做的一致性快照（WAL 下也安全）
- `conversation_histories.json`
- `runtime_settings.json`
- `search_health.json`
- `user_profiles/*.json`（`--no-profiles` 可跳过）

> 角色目录、`corpus/`、`private/.env` 不在本脚本范围：体积大/含密钥，另行备份。

## 恢复

1. 停止 NoneBot2（Dashboard「关闭 NoneBot2」或停止快捷方式）
2. 解压 zip，覆盖到 `qqbot/data/` 下对应文件
3. 重启 NoneBot2

## 建议

- 升级前、切换默认角色前、跑批量记忆抽取前，各备一次
- 备份文件可以按天保留；SQLite 快照很小（当前 7KB 级）
