# CHANGELOG

本文件记录公开版本的演进。私有实例的详细变更不在此列。

## 2.0.0

首个公开发布版。

### 核心
- SQLite schema V001：assets / asset_tags / asset_links / asset_events / verifications /
  history_sources / history_items / commands / errors / docs / * _fts / sync_log /
  health_runs / llm_memory。
- CLI `kb.py`：init / add / list / search / show / verify / deps / audit / health /
  backup / restore / sync / context / serve / doctor / import-* / export / scan-projects。
- 混合检索：名称精确 → 标签 → FTS5 trigram → LIKE → TF-IDF，附生命周期加权与命中原因。
- 依赖链：递归 CTE 双向查询（依赖 / 影响面）。
- Web 管理界面：仪表盘、资产、搜索、命令、故障、历史、文档、版本历史、健康、维护。

### 接入
- `scripts/hooks/` 三个钩子（SessionStart / UserPromptSubmit / Stop）。
- `scripts/trust_hooks.py`：查询客户端真实钩子哈希并写入信任配置，解决"静默不执行"。

### 运维
- 18 项健康检查，含密钥扫描、索引一致性、链接可用性。
- 备份/恢复（SQLite backup API，迁移前自动备份）。
- 版本审计：GitHub API 核查归档状态 / 最后推送 / 许可证 / 最新 release。

### 分发
- `scripts/build_public.py`：从私有实例生成脱敏公开版，含残留审计（发现即失败）。
- 规则外置到 `config/redact-rules.json`（不随公开产物分发）。
- 代码路径全部改为相对脚本位置 + 环境变量覆盖，可放任意目录。

### 修复
- 错误提取排除 ANSI 转义与代码回显，避免把正常输出误判为错误。
- `upsert_asset` 支持按 id 局部更新。
- PowerShell 脚本统一 UTF-8 with BOM（Windows PowerShell 5.1 否则按 ANSI 解析导致假语法错误）。
