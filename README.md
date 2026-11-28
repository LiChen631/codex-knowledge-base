# Codex Global Knowledge Base

一套**跨会话、跨项目复用**的本地知识资产系统：把日常使用 AI 编码助手时积累的工具、
命令、故障解法、GitHub 项目与工作流，沉淀成可检索、可验证、可持续更新的资产库。

> 纯本地运行，零第三方依赖（仅 Python 标准库），不使用任何付费 API 或云向量数据库。
> 所有数据保存在你自己机器上的一个 SQLite 文件里。

---


## 配套技能包（本系列）

知识库背后的专业技能已拆分为独立仓库，可直接安装使用（Agent Skills 格式，Claude Code / Codex 通用）：

| 仓库 | 内容 | 技能数 |
|---|---|---|
| [codex-cli-skills](https://github.com/your-org/codex-cli-skills) | 非交互驱动 Codex / Grok / Kimi / Hermes CLI | 4 |
| [mathmodel-paper-skills](https://github.com/your-org/mathmodel-paper-skills) | 数学建模竞赛论文全流程（CUMCM/MCM） | 10 |
| [docx-automation-skills](https://github.com/your-org/docx-automation-skills) | Word 精确编辑 + 后台窗口截图 | 2 |
| [packet-tracer-skill](https://github.com/your-org/packet-tracer-skill) | Cisco Packet Tracer 自动化 | 1 |
| [data-mining-lab-report-skill](https://github.com/your-org/data-mining-lab-report-skill) | 数据挖掘实验报告生成 | 1 |
| [agent-memory-skills](https://github.com/your-org/agent-memory-skills) | Agent 长期记忆管理 | 2 |

安装任意一个：

```bash
git clone https://github.com/your-org/<repo>.git /tmp/skills
cp -r /tmp/skills/skills/* ~/.agents/skills/
```

## 它能解决什么问题

| 痛点 | 本项目的做法 |
|---|---|
| 每次新开会话都要重新搜索同一个 GitHub 项目 | 资产库 + 混合检索，一条命令召回 |
| 反复安装已装过的工具、重复配置环境 | 记录 `install_status` / `verify_status`，已装的不再装 |
| 踩过的坑又踩一次 | 故障库按错误签名聚合，带出现次数与修复步骤 |
| 不知道以前某个任务是怎么做的 | 历史会话挖出的命令索引 + 工作流资产 |
| 经验只存在于聊天记录里，无法继承 | 会话启动时自动把相关经验注入上下文 |

## 核心能力

| 能力 | 实现 | 说明 |
|---|---|---|
| 结构化资产库 | SQLite + 版本化迁移 | 13 类资产，含生命周期与事件流 |
| 中文全文检索 | FTS5 `trigram` | 中文无需分词即可子串匹配 |
| 语义检索（本地免费） | 字符二元组 TF-IDF | 零依赖，不调用嵌入 API |
| 混合排序 | 精确 + FTS + LIKE + TF-IDF + 生命周期加权 | 结果带 `why` 说明命中原因 |
| 依赖链查询 | SQLite 递归 CTE | 双向：它依赖谁 / 谁依赖它 |
| 本地 Web 界面 | 标准库 `http.server` + 原生前端 | 只监听 127.0.0.1 |
| 会话自动接入 | Hooks（SessionStart / UserPromptSubmit / Stop） | 与项目、目录无关 |
| 健康检查 | 18 项 + 月度报告 | 含密钥扫描与索引一致性 |
| 备份 / 恢复 | SQLite backup API + zip | 迁移前自动备份 |
| 版本审计 | GitHub API 实时核查 | 归档/停更/许可证自动标记 |

## 快速开始

```bash
git clone https://github.com/<your-org>/codex-global-knowledge-base.git
cd codex-global-knowledge-base

python scripts/kb.py init          # 建库 + 从 seed.json 恢复资产
python scripts/kb.py doctor        # 看状态
python scripts/kb.py search "docker"   # 检索
python scripts/kb.py serve --open  # http://127.0.0.1:8787/
```

要求：Python 3.10+（推荐 3.12），SQLite 3.34+（Python 自带即可，需 FTS5 trigram）。
无需 pip 安装任何包。

### Windows 一键启动

```powershell
powershell -File scripts\start-dashboard.ps1
```

## 接入你的 AI 编码助手（可选但推荐）

以 Codex 为例，修改 `~/.codex/hooks.json` 加入三个钩子：

```json
{
  "hooks": {
    "SessionStart": [{ "hooks": [{ "type": "command",
      "command": "python \"<KB-ROOT>/scripts/hooks/kb_session_start.py\"", "timeout": 20 }] }],
    "UserPromptSubmit": [{ "hooks": [{ "type": "command",
      "command": "python \"<KB-ROOT>/scripts/hooks/kb_prompt_submit.py\"", "timeout": 15 }] }],
    "Stop": [{ "hooks": [{ "type": "command",
      "command": "python \"<KB-ROOT>/scripts/hooks/kb_stop.py\"", "timeout": 15 }] }]
  }
}
```

**关键一步**：多数客户端不会执行未经验证的钩子。Codex 需要把钩子哈希写入
`config.toml` 的 `[hooks.state.'<key>'] trusted_hash`：

```bash
python scripts/trust_hooks.py "<你的项目目录>"
```

该脚本会向 Codex 的 app-server 查询真实哈希并自动写入、再校验 `trustStatus=trusted`。
钩子未经信任会被**静默跳过**（不报错），这是最常见的"不生效"原因。

## 目录结构

```
config/       settings / categories / sync-policy
database/     knowledge.db（本地，含历史，不入库）+ migrations/ + seed.json（版本化）
scripts/      kb.py(CLI) kbcore.py(核心) scan_history.py import_local_assets.py
              reports.py health_check.py audit.py scan_projects.py
              build_public.py trust_hooks.py hooks/
dashboard/    serve.py + static/index.html
docs/         架构 / 使用 / 维护 / 安全策略
reports/      数据驱动生成的分析报告
tests/        单元测试 + 端到端接入测试
```

## 为什么数据库不进 Git

`knowledge.db` 会包含你的历史会话条目（数万行），体积大且含个人工作路径。
因此仓库只版本化 `database/seed.json`（精选资产，数百 KB）：

- 新机器 `python scripts/kb.py init` 会自动从 `seed.json` 恢复资产；
- 历史条目用 `python scripts/kb.py import-history` 从你自己的会话重建。

## 数据来源

| 来源 | 说明 |
|---|---|
| Codex 会话数据库 | `~/.codex/thread_history_1.sqlite`（结构化 item） |
| Codex rollout | `~/.codex/sessions/**/rollout-*.jsonl[.zst]` |
| 其他 Agent | 可扩展适配器（示例：DeepSeek Harness 的 zstd 会话） |
| 本机资产 | 技能目录 / 插件清单 / MCP 配置 / 软件版本探测 |

## 设计原则

1. **状态诚实**：`discovered` / `installed` / `verified` 严格区分；只有真实执行成功才标 `verified`。
2. **复用优先**：已装且验证 → 已装未验证 → 历史验证过 → 候选 → GitHub → 原创。
3. **零依赖**：仅 Python 标准库，避免长期腐化。
4. **本地优先**：Web 只绑回环地址；不上传任何数据。
5. **可回滚**：迁移前自动备份；配置修改前自动备份；Git 保留完整历史。

## 当前局限

- 语义检索是 TF-IDF，不是神经嵌入，同义不同词召回有限。
- 历史挖掘目前只覆盖 Codex 与会话文件格式明确的 Agent；其他客户端需自行写适配器。
- Web 界面为只读优先设计，写操作需显式 `--allow-write`。

## 许可

MIT（见 `LICENSE`）。示例数据仅用于演示结构，不含任何真实业务信息。
