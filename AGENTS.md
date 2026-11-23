# 本仓库的 Agent 规则

本仓库是**知识库系统本体**（可移植模板），不是业务项目。

## 修改规约

1. 改 schema 必须新增 `database/migrations/Vxxx__*.sql`，不要改已应用的迁移。
2. 改完 schema 运行 `python scripts/kb.py migrate`，并确认 `python scripts/kb.py health` 通过。
3. 禁止在 `config/`、`docs/`、`reports/`、`database/seed.json` 中写入任何密钥、令牌、密码。
4. 修改 hook 脚本后必须运行 `python scripts/trust_hooks.py <cwd>` 重建信任，否则客户端会静默跳过。
5. 新增/修改脚本后至少运行一次对应 CLI 子命令，确认无异常再提交。
6. `reports/` 与各分类 `index.md` 由脚本生成，不要手改；要改内容就改生成器。
7. 任何"已验证"结论必须有实际执行输出，并记录在 `verifications` 表。
8. 不要提交 `database/knowledge.db`、`backups/`、`logs/`。

## 路径约定

- 代码一律用「相对脚本自身位置」推导根目录，不写死盘符。
- 需要外部目录时用环境变量：`KB_ROOT`、`KB_DB`、`KB_SCAN_ROOTS`、`KB_SCRIPT_DIRS`。
