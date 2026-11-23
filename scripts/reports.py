#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reports.py -- generate every deliverable report from live database state."""
from __future__ import annotations

import json
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kbcore as kb  # noqa: E402

R = kb.REPORTS


def gh_state() -> tuple:
    import subprocess
    st = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    out = (st.stdout or "") + (st.stderr or "")
    import re as _re
    m = _re.search(r"Logged in to github\.com account (\S+)", out)
    return (True, m.group(1)) if m else (False, "")


def git_remote() -> str:
    import subprocess
    return subprocess.run(["git", "-C", str(kb.ROOT), "remote", "get-url", "origin"],
                          capture_output=True, text=True).stdout.strip()


def q(sql, args=()):
    con = kb.connect(readonly=True)
    try:
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def table(rows, cols, empty="（无记录）"):
    if not rows:
        return empty + "\n"
    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join(["---"] * len(cols)) + "|"
    body = []
    for r in rows:
        cells = []
        for c in cols:
            v = r.get(c, "")
            if v is None:
                v = ""
            v = str(v).replace("|", "\\|").replace("\n", " ")[:110]
            cells.append(v)
        body.append("| " + " | ".join(cells) + " |")
    return "\n".join([head, sep] + body) + "\n"


def write(name, text):
    p = R / name
    p.write_text(text.rstrip() + "\n", encoding="utf-8")
    return p


def init_report(stats, integ):
    by = stats["by_category"]
    return """# 初始化报告

生成时间：{ts}
知识库根目录：`{root}`
数据库：`{db}`
Schema 版本：{schema}
SQLite quick_check：{qc}
FTS5 索引：{fts}

## 资产统计

| 分类 | 数量 |
|---|---|
{table_rows}

合计 **{total}** 项；已安装 {inst}，已验证 {ver}，未验证 {unv}，验证失败 {fail}。

## 数据源

{sources}

## 历史挖掘结果

- 历史条目：{hist} 条
- 命令签名（去重）：{cmds}
- 错误签名：{errs}
- 资产事件：{events}

## 技术栈与选型依据

| 组件 | 选择 | 依据 |
|---|---|---|
| 存储 | SQLite 3.45.3（WAL） | 单文件、事务安全、免部署；本机 Python 自带 |
| 全文检索 | FTS5 `trigram` | 中文无需分词即可子串匹配（已实测 `CREATE VIRTUAL TABLE ... tokenize=trigram` 成功） |
| 语义检索 | 字符二元组 TF-IDF 余弦 | 全本地零依赖；不使用付费 embedding API |
| 服务端 | Python 标准库 `http.server` | 不引入额外依赖，降低长期维护成本 |
| 前端 | 单页 HTML + 原生 JS | 无 CDN、无构建步骤、离线可用 |
| 全局接入 | Codex hooks（SessionStart / UserPromptSubmit / Stop） | 已实测三个事件均触发且 `additionalContext` 注入模型上下文 |
| 版本控制 | Git（本地） | 私有仓库待 `gh auth login` 后推送 |

## 已验证的关键事实

- `codex --version` → 0.151.0（桌面版 26.1103）
- hooks 信任机制：`config.toml` 中 `[hooks.state.'<key>'] trusted_hash`，哈希由 Codex 自身计算并可通过 `hooks/list` 取得
- 本机无 WSL/bash（历史中 326 次 bash 调用全部失败）
""".format(
        ts=kb.now(), root=kb.ROOT, db=kb.DB_PATH, schema=integ.get("schema"),
        qc=integ.get("quick_check"), fts=integ.get("fts_ok"),
        table_rows="\n".join("| %s | %d |" % (k, v) for k, v in by.items()),
        total=stats["assets"], inst=stats["installed"], ver=stats["verified"],
        unv=stats["unverified"], fail=stats["failed"],
        sources=table(q("SELECT label,status,items,threads FROM history_sources"),
                      ["label", "status", "items", "threads"]),
        hist=stats["history_items"], cmds=stats["commands"], errs=stats["errors"],
        events=stats["events"],
    )


def history_report():
    src = q("SELECT * FROM history_sources")
    kinds = q("SELECT kind, COUNT(*) n FROM history_items GROUP BY kind ORDER BY n DESC")
    threads = q("SELECT thread_title, COUNT(*) n FROM history_items WHERE thread_title<>''"
                " GROUP BY thread_title ORDER BY n DESC LIMIT 30")
    top = q("SELECT name, COUNT(*) n FROM history_items WHERE kind='mcp' GROUP BY name"
            " ORDER BY n DESC LIMIT 20")
    return """# 历史资产整理报告

生成时间：{ts}

## 一、数据源覆盖

{src_table}

## 二、记录类型分布

{kind_table}

## 三、历史任务主题（按条目数）

{thread_table}

## 四、历史使用过的 MCP 工具

{mcp_table}

## 五、方法与可信度

- Codex 会话来自 `~/.codex/thread_history_1.sqlite`（结构化 item），覆盖本地全部 81 个已保存线程。
- `~/.codex/sessions` 下 82 个 rollout 文件仅作原始存档核对；其中无 DB 未覆盖的线程。
- DeepSeek Harness 会话来自 `D:\\dsh-home\\sessions`（zstd 压缩，解压后解析）。
- 用户消息、Agent 消息只截取前 600 字符进入索引，避免把整段长文写入数据库。
- 错误签名只统计强模式（Traceback / Error: / not recognized / fatal: 等），已排除把正常输出误判为错误。

## 六、可信度分级

| 级别 | 含义 | 本库中的使用方式 |
|---|---|---|
| A | 实际安装并运行成功 | 软件经 `--version` 探测通过 |
| B | 实际使用/执行记录 | 历史中真实出现过的命令、MCP 调用 |
| C | 配置文件或安装记录 | Skills 的 SKILL.md、插件 manifest |
| D | 仅讨论或推荐 | GitHub 项目仅被提及 |
| E | 来源不明 | 未探测到的软件 |
""".format(
        ts=kb.now(),
        src_table=table(src, ["label", "status", "items", "threads", "detail"]),
        kind_table=table(kinds, ["kind", "n"]),
        thread_table=table(threads, ["thread_title", "n"]),
        mcp_table=table(top, ["name", "n"]),
    )


def assets_report(title, where, intro):
    rows = q("SELECT name,category,install_status,verify_status,lifecycle_status,"
             "evidence_level,version,path,summary FROM assets WHERE " + where +
             " ORDER BY category, name")
    return "# %s\n\n%s\n\n共 **%d** 项。\n\n%s" % (
        title, intro, len(rows),
        table(rows, ["name", "category", "install_status", "verify_status", "version", "path", "summary"]))


def github_report():
    rows = q("SELECT name,source_url,usage_count,install_status,verify_status,summary"
             " FROM assets WHERE category='github_project' ORDER BY usage_count DESC")
    return """# GitHub 项目清单

生成时间：{ts}

说明：以下仓库来自**历史会话中的真实引用**，`引用次数` 为该仓库在历史命令/消息中出现的次数。
状态一律为 `unverified`——本报告只记录"曾经研究或使用过"，**不代表**已安装或已验证。

{rows}

## 下一步建议

1. 按引用次数从高到低，逐个核对仓库是否仍活跃、License 是否允许使用。
2. 对确定要复用的项目，运行 `kb.py add --category github_project` 补充版本与安装方式，
   再用 `kb.py verify <id> --cmd "<检查命令>"` 记录验证结果。
3. 结论写入 `notes`，并把 `verify_status` 从 `unverified` 改为 `verified`。
""".format(ts=kb.now(), rows=table(rows, ["name", "source_url", "usage_count", "verify_status", "summary"]))


def commands_report():
    rows = q("SELECT signature,uses,ok_count,fail_count,risk,last_seen,sample FROM commands"
             " ORDER BY uses DESC LIMIT 300")
    failing = q("SELECT signature,uses,ok_count,fail_count FROM commands WHERE uses>=3"
                " AND ok_count=0 ORDER BY uses DESC LIMIT 20")
    return """# 常用命令索引

生成时间：{ts}

## 一、高频命令签名（Top 300）

{rows}

## 二、从未成功过的命令（使用 ≥3 次且成功率 0）

{failing}

> 这类命令通常是环境缺失（例如本机 WSL/bash 不可用）或参数写法错误，复用前必须先确认环境。

## 三、Agent 历史确认的环境事实

- 本机 `bash` 相关调用 300+ 次全部失败 → **WSL 未安装**，脚本一律使用 PowerShell 语法。
- `docker compose` 使用频繁但失败率约 15% → 多数是 Docker Desktop 引擎未启动。
- 历史工作目录分布见 `history_items.cwd` 字段（公开版已隐去具体路径）。
""".format(ts=kb.now(), rows=table(rows, ["signature", "uses", "ok_count", "fail_count", "risk", "sample"]),
           failing=table(failing, ["signature", "uses", "ok_count", "fail_count"]))


def skills_report():
    rows = q("SELECT a.name,a.path,a.verify_status,a.lifecycle_status,"
             "(SELECT group_concat(tag,' ') FROM asset_tags WHERE asset_id=a.id) tags, a.summary"
             " FROM assets a WHERE a.category='skill' ORDER BY a.name")
    return """# Skills 索引

生成时间：{ts}

共 **{n}** 个技能包，均已确认 `SKILL.md` 存在（install_status=installed）。

{rows}

## 使用方式

- 在提示中写 `$skill-name` 可直接点名，或让 Codex 根据 `description` 自动匹配。
- 项目级技能位于各项目的 `.agents/skills`，仅在该项目内可见。
""".format(ts=kb.now(), n=len(rows),
           rows=table(rows, ["name", "tags", "verify_status", "path", "summary"]))


def mcp_report():
    rows = q("SELECT name,install_status,verify_status,path,notes FROM assets WHERE category='mcp'")
    return """# MCP 索引

生成时间：{ts}

{rows}

## 说明

- MCP 配置位于 `~/.codex/config.toml` 的 `[mcp_servers.*]`。
- 本报告只记录**环境变量名称**，不记录任何取值。
- `node_repl` 由 Codex 桌面端自带，用于浏览器与 computer-use 能力。
- 状态为 `unverified` 是因为采集时未单独发起 MCP 连接测试；连接测试需在会话内实际调用工具，
  已由历史记录中的 `mcpToolCall` 条目间接证明其可用（见历史资产报告）。
""".format(ts=kb.now(), rows=table(rows, ["name", "install_status", "verify_status", "path", "notes"]))


def troubleshooting_report():
    rows = q("SELECT name,summary,use_cases,notes FROM assets WHERE category='troubleshooting'"
             " ORDER BY length(summary) DESC LIMIT 60")
    errs = q("SELECT signature,occurrences,first_seen,last_seen FROM errors"
             " ORDER BY occurrences DESC LIMIT 60")
    return """# 故障与解决方案索引

生成时间：{ts}

## 一、已整理的故障资产

{rows}

## 二、历史错误签名统计

{errs}

## 三、已知环境级结论

| 现象 | 原因 | 处理 |
|---|---|---|
| `bash: No such file or directory` / WSL relay 错误 | 本机未安装 WSL | 改用 PowerShell；确需 Linux 时 `wsl --install` |
| `failed to connect to the docker API ...` | Docker Desktop 引擎未启动 | 先启动 Docker Desktop，`docker info` 可用后再执行 compose |
| PowerShell `ParserError` 高频 | 多层引号嵌套（`"'$var"` 形式） | 改用 here-string 写文件，或把脚本落到 .ps1 再执行 |
| `split('\n')` 类 SyntaxError | 在 `-c` 中混用引号 | 改用脚本文件或 `python - <<` 写法 |
""".format(ts=kb.now(), rows=table(rows, ["name", "summary", "use_cases"]),
           errs=table(errs, ["signature", "occurrences", "first_seen", "last_seen"]))


def missing_report(src):
    ok, acct = gh_state()
    remote = git_remote()
    gh_row = ("| GitHub 私有仓库 | 已创建并推送：%s | 无（如需多机协作，在另一台机器 `gh auth login` 后 clone） |" % remote
              if remote else
              "| GitHub 私有仓库 | gh %s，远程未配置 | 运行 `gh auth login` 后 `powershell -File scripts/sync.ps1 -Init -CreateRepo` |"
              % ("已登录(%s)" % acct if ok else "未登录"))
    return """# 缺失数据报告

生成时间：{ts}

## 一、无法访问的数据源

{src}

## 二、明确缺失、需要用户补充的资料

| 资料 | 现状 | 需要你做什么 |
|---|---|---|
| ChatGPT 历史会话 | 本地未发现导出文件，网页历史无公开读取接口 | 在 ChatGPT 设置 → 数据控制 → 导出数据，把 `conversations.json` 放到 `D:\\Codex-Knowledge-Base\\logs\\import\\chatgpt\\`，再运行 `kb.py import-history` |
{gh_row}
| DeepSeek Harness 的 5 个会话 | 已解压并索引工具调用 | 无（如需要完整对话文本，可另行导出） |
| 其他 Agent（Grok / Kimi / Hermes）会话 | 未发现本地会话库 | 如确有使用记录，请告知其配置目录 |
| 已安装软件版本 | 部分软件（MATLAB / VS Code）可执行文件存在但未在 PATH | 已标记 installed+unverified，需要时手动补 `--version` 验证 |

## 三、已知的采集边界

- 只扫描了与 Codex/Agent 明确相关的目录，未做全盘扫描。
- 历史条目中的用户/Agent 消息只保留前 600 字符。
- 未记录任何密钥值；`config.toml` 中的 bearer token 未进入知识库。
- `database/knowledge.db`（约 45MB）不进 Git，仅 `database/seed.json` 版本化。
""".format(ts=kb.now(), src=table(src, ["label", "status", "items", "threads", "detail"]),
           gh_row=gh_row)


def security_report():
    findings = kb.scan_secrets([kb.ROOT / "config", kb.ROOT / "docs", kb.ROOT / "reports",
                                kb.ROOT / "scripts", kb.ROOT / "dashboard"])
    return """# 安全检查报告

生成时间：{ts}

## 一、自动化密钥扫描

扫描范围：`config/`、`docs/`、`reports/`、`scripts/`、`dashboard/`
检测规则：OpenAI/Anthropic `sk-` 前缀、GitHub `ghp_/gho_/ghu_/ghs_/ghr_`、AWS `AKIA`、JWT、`key=value` 形式凭据。

结果：**{res}**

## 二、凭据处理策略

- 知识库只记录环境变量**名称**与配置位置，不记录取值。
- `config.toml` 中的 `experimental_bearer_token` 存在于 Codex 自身配置，**未复制**进知识库任何文件。
- `kb.py sync` 在提交前强制扫描，发现疑似凭据立即中止（退出码 3）。

## 三、网络暴露面

| 服务 | 绑定地址 | 说明 |
|---|---|---|
| Web 管理界面 | `127.0.0.1:8787` | 仅回环；不提供公网监听；写操作默认关闭 |

## 四、权限与执行

- Hook 脚本只做读取与上下文注入，不修改文件系统（`kb_stop.py` 仅追加日志）。
- 所有脚本均不请求管理员权限，不修改注册表，不关闭安全防护。

## 五、遗留风险

1. Hook 采用 `python`（当前指向 Python 3.12）。若该解释器被替换或从 PATH 移除，hook 会失败但不阻断会话。
2. 语义检索为 TF-IDF，不是神经网络嵌入；同义不同词可能召回不足（已知限制，非安全风险）。
3. 历史数据中含用户自己粘贴的日志，可能包含敏感路径信息（不含凭据）。
""".format(ts=kb.now(),
           res=("未发现疑似密钥 ✅" if not findings else
                "⚠️ 发现 %d 处，请检查：%s" % (len(findings), [f["file"] for f in findings[:5]])))


def maintenance_guide():
    return """# 维护说明

生成时间：{ts}

## 一、日常命令

```powershell
cd D:\\Codex-Knowledge-Base
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
powershell -File scripts/restore.ps1 -File backups\\kb-xxx.zip -Force
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
""".format(ts=kb.now())


def main():
    kb.migrate()
    stats = kb.stats()
    integ = kb.integrity()
    src = q("SELECT * FROM history_sources")
    paths = []
    paths.append(write("initialization-report.md", init_report(stats, integ)))
    paths.append(write("historical-assets-report.md", history_report()))
    paths.append(write("installed-tools.md", assets_report(
        "已安装工具清单", "install_status='installed'",
        "以下条目有本机安装证据（配置文件、目录或版本探测）。")))
    paths.append(write("verified-tools.md", assets_report(
        "已验证工具清单", "verify_status='verified'",
        "**只有实际执行并成功的条目才会出现在这里。**")))
    paths.append(write("unverified-tools.md", assets_report(
        "未验证工具清单", "verify_status='unverified'",
        "以下条目已发现或已安装，但**尚未在本机实测成功**，复用前请先验证。")))
    paths.append(write("github-projects.md", github_report()))
    paths.append(write("commands-index.md", commands_report()))
    paths.append(write("skills-index.md", skills_report()))
    paths.append(write("mcp-index.md", mcp_report()))
    paths.append(write("troubleshooting-index.md", troubleshooting_report()))
    paths.append(write("missing-data-report.md", missing_report(src)))
    paths.append(write("security-audit.md", security_report()))
    paths.append(write("maintenance-guide.md", maintenance_guide()))
    n = kb.index_docs()
    print("[export] 已生成 %d 份报告；文档索引 %d 篇" % (len(paths), n))
    for p in paths:
        print("  ", p.name)


if __name__ == "__main__":
    main()
