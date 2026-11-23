# 维护说明

生成时间：2026-11-23T10:20:49+08:00

## 一、日常命令

```powershell
cd <kb-root>
python scripts/kb.py doctor                 # 诊断
python scripts/kb.py search "关键词"        # 检索
python scripts/kb.py import-history         # 重新扫描历史（幂等）
python scripts/kb.py import-assets          # 重新采集本机资产
python scripts/kb.py health                 # 健康检查
python scripts/kb.py health --monthly       # 月度检查并写报告
python scripts/kb.py backup --tag manual    # 备份
python scripts/kb.py export                 # 重新生成全部报告
python scripts/kb.py serve --open           # 打开管理界面
```

## 二、数据库迁移

1. 在 `database/migrations/` 新增 `V002__xxx.sql`。
2. 运行 `python scripts/kb.py migrate`（会自动先备份）。
3. 回滚：`python scripts/kb.py restore backups/<最新>.zip --force`。

## 三、Hook 变更后的信任

修改 `~/.codex/hooks.json` 后必须重新信任，否则 Codex 不会执行：

```powershell
python scripts/trust_hooks.py "<你的项目目录>"
```

脚本会向 Codex 查询真实哈希并写入 `config.toml`，随后自动校验 `trustStatus=trusted`。
**注意**：写入前会自动备份 `config.toml`；如果 Codex 升级后哈希算法变化，重跑本脚本即可。

## 四、备份与恢复

```powershell
powershell -File scripts/backup.ps1                 # 备份到 backups/
powershell -File scripts/restore.ps1 -Latest        # 恢复最新备份
powershell -File scripts/restore.ps1 -File backups\kb-xxx.zip -Force
```

备份内容：数据库（用 SQLite backup API 保证一致性）、config、migrations、根文档。
默认保留最近 30 份。

## 五、Git 同步

```powershell
powershell -File scripts/sync.ps1            # 提交（含密钥扫描）
powershell -File scripts/sync.ps1 -Push      # 提交并推送
powershell -File scripts/sync.ps1 -Init -CreateRepo   # 首次初始化私有仓库（需 gh 已登录）
```

## 六、月度健康检查

`SessionStart` hook 会在当月首次会话时提示执行 `kb.py health --monthly`。
报告写入 `reports/monthly/YYYY-MM.md`，并记录到 `health_runs` 表。
若某月未执行，下次执行时会补写当月的报告（以实际运行日期为准）。

## 七、新增资产的三种方式

1. CLI：`python scripts/kb.py add --name ... --category ...`
2. Web：`kb.py serve --allow-write` 后使用界面
3. 批量：写 JSON 后 `python scripts/kb.py add --json data.json`

## 八、故障排查顺序

1. `kb.py doctor`（数据库 / 索引 / git / 资产统计）
2. `kb.py health`（18 项检查）
3. 检查 `logs/turn_trail.jsonl` 是否有新记录（判断 Stop hook 是否工作）
4. 检查 `~/.codex/config.toml` 的 `[hooks.state.*]` 是否与 `trust_hooks.py` 输出一致
