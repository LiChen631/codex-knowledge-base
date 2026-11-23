# 架构说明

## 1. 分层

```
                    ┌──────────────────────────────────────────┐
                    │  Codex 会话（任意项目 / 任意目录）        │
                    │  ~/.codex/AGENTS.md  全局规则             │
                    └───────────────┬──────────────────────────┘
                                    │ hooks（SessionStart /
                                    │ UserPromptSubmit / Stop）
                    ┌───────────────▼──────────────────────────┐
                    │  scripts/hooks/*.py  ── 生成 additionalContext
                    └───────────────┬──────────────────────────┘
                                    │ import
                    ┌───────────────▼──────────────────────────┐
                    │  scripts/kbcore.py  核心库                │
                    │   ├─ 迁移 / 资产 CRUD / 事件 / 验证        │
                    │   ├─ 混合检索（精确+FTS5+LIKE+TF-IDF）     │
                    │   └─ 密钥扫描 / 备份恢复 / 文档索引        │
                    └───────┬───────────────────────┬──────────┘
                            │                       │
              ┌─────────────▼──────────┐  ┌─────────▼─────────────┐
              │ database/knowledge.db  │  │ dashboard/serve.py    │
              │ SQLite(WAL)+FTS5       │  │ 127.0.0.1:8787        │
              └─────────────┬──────────┘  └───────────────────────┘
                            │
              ┌─────────────▼────────────────────────────────────┐
              │ 数据来源                                          │
              │ • ~/.codex/thread_history_1.sqlite（结构化会话）   │
              │ • ~/.codex/sessions/**/rollout-*.jsonl[.zst]     │
              │ • D:\dsh-home\sessions/**/session.jsonl.zstd     │
              │ • ~/.codex/config.toml（插件/MCP 声明）           │
              │ • 本机目录（skills / scripts / tools）            │
              └──────────────────────────────────────────────────┘
```

## 2. 数据模型

| 表 | 作用 | 关键点 |
|---|---|---|
| `assets` | 统一资产记录（13 类） | `UNIQUE(name, category)`；`lifecycle_status` 表达生命周期 |
| `asset_tags` | 自由标签 | 支持按来源/项目/类型过滤 |
| `asset_links` | 资产关系图 | `depends_on / provides / related / uses / replaced_by`，可查完整依赖链 |
| `asset_events` | 生命周期事件流 | discovered / installed / verified / failed / deprecated ... |
| `verifications` | 验证记录 | 只有 `result=pass` 才会把资产置为 verified |
| `history_sources` | 数据源清单 | 记录 status（scanned/partial/inaccessible）与规模 |
| `history_items` | 历史条目 | command / mcp / file / websearch / message / agent |
| `commands` | 命令签名聚合 | `tool + verb` 去重，统计 ok/fail |
| `errors` | 错误签名聚合 | 强模式匹配，避免把正常输出当错误 |
| `assets_fts` / `history_fts` / `docs_fts` | FTS5 trigram 索引 | 中文子串检索无需分词 |
| `health_runs` | 健康检查历史 | 月度报告关联 |
| `llm_memory` | 长期记忆条目 | scope = global / project:<path> |

## 3. 检索流程

```
query
  ├─ 1. 名称完全匹配         (+100)
  ├─ 2. 名称包含             (+40)
  ├─ 3. 标签匹配             (+35)
  ├─ 4. FTS5 trigram MATCH   (+25)   ← 查询 ≥3 字符时
  ├─ 5. 描述字段 LIKE        (+12)
  ├─ 6. TF-IDF 余弦相似      (+0~30)
  └─ 7. 生命周期与使用次数加权
         in_use +12 / verified +10 / installed +8 / expired −10 / deprecated −14
```

结果按分数降序返回，并附带 `why` 字段说明命中原因，便于人工判断可信度。

## 4. 全局接入机制（已实测）

| 事件 | 脚本 | 作用 |
|---|---|---|
| `SessionStart` | `kb_session_start.py` | 注入知识库规模、当前项目相关资产、月度检查提醒、高频历史故障 |
| `UserPromptSubmit` | `kb_prompt_submit.py` | 按当轮提示检索最多 3 条相关资产 |
| `Stop` | `kb_stop.py` | 追加一行 turn trail 到 `logs/turn_trail.jsonl`，供后续挖掘 |

**信任机制**：Codex 只执行 `config.toml` 中记录过 `trusted_hash` 的 hook。
哈希不是简单 sha256(命令)，而是 Codex 内部对 hook 定义计算的值 → 通过 `hooks/list` 方法取得，
由 `scripts/trust_hooks.py` 自动写入并校验。

## 5. 安全边界

- Web 服务只监听回环地址；写操作需 `--allow-write` + 可选 `X-KB-Token`。
- 数据库、Markdown、Git 提交前统一走 `scan_secrets()`。
- hook 脚本任何异常都输出 `{"continue": true}` 或降级提示，绝不阻断会话。
