# 安全策略

## 凭据

- 禁止把 API Key / Token / 密码写入 `assets`、Markdown、日志或 Git。
- 只记录：环境变量名、配置位置、配置方法、是否已配置。
- `kbcore.scan_secrets()` 在以下时机自动运行：`kb.py sync`（提交前）、`kb.py health`（第 17 项检查）。
- 检测到疑似凭据时：`sync` 退出码 3 并列出文件与行号（不打印明文）。

## 执行

- 所有 hook 脚本异常时降级，不阻断会话。
- `kb_stop.py` 只追加写 `logs/turn_trail.jsonl`。
- 不自动卸载、不自动删除资产；过期只做标记。

## 网络

- Web 服务默认 `127.0.0.1`。
- 健康检查的网络请求仅在 `--network` 或 `--monthly` 时发生，且只对 GitHub 仓库做 HEAD 探测。
- 不向任何第三方上传数据。

## 数据边界

- 只采集与 Agent/开发明确相关的目录。
- 历史消息截断存储（600 字符），降低隐私面。
- 备份文件包含完整数据库，应视为敏感文件，不要上传到公开位置。

## 变更管理

- `config.toml` 由 `trust_hooks.py` 修改前自动备份为 `config.toml.bak-kb`。
- 数据库迁移前自动备份。
- 任何结构性变更都要在 `CHANGELOG.md` 记录。
