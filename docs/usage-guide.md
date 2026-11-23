# 使用指南

## 1. 每次开始任务时

知识库会通过 `SessionStart` hook 自动把摘要注入上下文（你会看到 `[KB] ...` 开头的段落）。
如果你看到的摘要里已经命中相关资产，直接从那里开始，不必重新搜索。

手动检索：

```powershell
python D:\Codex-Knowledge-Base\scripts\kb.py search "关键词" --history
```

`--history` 会把历史会话条目也纳入结果。

## 2. 常用查询

```powershell
# 看某类资产
python scripts\kb.py list --category skill
python scripts\kb.py list --category software --verified
# 看详情（含事件流、验证记录、依赖关系）
python scripts\kb.py show skill:openai-docs
# 执行验证
python scripts\kb.py verify software:docker --cmd "docker --version"
```

## 3. 添加资产

```powershell
python scripts\kb.py add `
  --name "my-tool" --category software `
  --summary "做什么用的" --use-cases "什么时候用" `
  --url "https://github.com/owner/repo" `
  --install "winget install xxx" `
  --invoke "my-tool --help" `
  --evidence C --install-status installed `
  --tags "windows,cli"
```

添加后建议立刻验证：

```powershell
python scripts\kb.py verify software:my-tool --cmd "my-tool --version"
```

## 4. Web 界面

```powershell
python scripts\kb.py serve --open              # 只读
python scripts\kb.py serve --allow-write       # 允许修改（建议同时设置 KB_DASH_TOKEN）
```

页面：仪表盘 / 资产分类 / 搜索 / 命令索引 / 故障库 / 历史记录 / 文档 / 健康检查 / 维护。

## 5. 代码调用

```python
import sys
sys.path.insert(0, r"D:\Codex-Knowledge-Base\scripts")
import kbcore as kb

kb.search("docker compose", limit=5)          # 混合检索
kb.build_context("做数学建模", limit=5)        # 生成注入用上下文
kb.stats()                                    # 统计
kb.get_asset("skill:openai-docs")             # 详情
```

## 6. 常见问题

| 现象 | 处理 |
|---|---|
| 会话里没有 `[KB]` 段 | 运行 `python scripts\trust_hooks.py <cwd>` 重建信任，再新开会话 |
| 搜索没结果 | 换更短的关键词；中文可用 2 字词；加 `--history` |
| 健康检查报索引不同步 | `python scripts\kb.py init` 重建文档索引；资产索引随 upsert 自动同步 |
| 数据库损坏 | `powershell -File scripts\restore.ps1 -Latest` |

## 7. 版本审计与依赖链

```powershell
# 用 gh api 核查全部 GitHub 项目：归档状态、最后推送、星标、许可证、最新 release
python scripts\kb.py audit

# 查看某资产的依赖链（向下 = 它依赖谁；向上 = 谁依赖它，即改动影响面）
python scripts\kb.py deps skill:paper-workflow-orchestrator
python scripts\kb.py deps software:python --json

# 扫描本机已有项目的接入状况（只读，不改业务仓库）
python scripts\kb.py scan-projects
```

审计规则：归档 → `deprecated`；>540 天无推送 → `expired`；>365 天 → `needs_update`。
**审计只改状态，不卸载、不替换任何工具。**
