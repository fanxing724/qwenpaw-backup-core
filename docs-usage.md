# 备份核心（backup-core）

把重要文件备份到 **Gitee / GitHub 私有仓库**：插件采集并脱敏 → Agent 转换为可读格式 → git over SSH 推送。

## 工作流（Agent 三段式）

1. `POST /api/backup-core/collect`：采集 companion 数据（state/schedule/routine/notes/diary/weekly/dreams/history/interactions/usage/profiles）与工作区重要文件（MEMORY/PROFILE/AGENTS/SOUL/jobs.json/技能），**脱敏**后入暂存区，并生成确定性可读版 `READABLE.md`
2. Agent 调 `backup_core_get_draft`（或 `GET /draft`）读脱敏草稿 → 用自已的话转换成 `AGENT-NOTES.md`（`backup_core_submit_markdown` / `POST /markdown`）
3. `backup_core_push`（或 `POST /push`）：暂存区 commit + push 到所有已配置远端

## 安全边界

- **密钥永不上传**：organizer `api_key` → `***`、care `token`/`model_signature` 剔除；提交 markdown 时命中密钥特征直接 400
- 只推设置里显式配置的远端；SSH BatchMode，不弹凭据
- 单文件 ≤2 MiB、单次 ≤400 文件 / 24 MiB
- 备份缓存与暂存都在插件数据目录内（`~/.qwenpaw/data/backup-core/`）

## 配置

`PUT /api/backup-core/settings`：

```json
{
  "targets": [{"name": "github", "url": "git@github.com:<user>/<private-repo>.git", "branch": "main"},
              {"name": "gitee",  "url": "git@gitee.com:<user>/<private-repo>.git",  "branch": "main"}],
  "companion": true,
  "workspace_globs": ["MEMORY.md", "PROFILE.md", "AGENTS.md", "SOUL.md", "jobs.json", "skills/companion_core/SKILL.md"],
  "workspace_root": "",
  "git_name": "番星", "git_email": "3509412293@qq.com",
  "key_path": "",
  "auto": false, "time": "03:30"
}
```

- `workspace_root` 留空 = 宿主 WORKING_DIR
- `key_path` 留空 = `~/.ssh/id_ed25519`
- 空仓库首推自动创建分支；无变化时 `no-change` 不产生空提交

## 端点与工具

| 端点 | 说明 |
|---|---|
| GET/PUT `/settings` | 配置读写 |
| POST `/collect` | 采集+脱敏+可读版 |
| GET `/draft` | 脱敏草稿（Agent 转换用） |
| POST `/markdown` | 提交 Agent 转换结果 |
| POST `/push` | 推送到所有远端 |
| GET `/status` | 最近 30 次运行记录 |

Agent 工具（默认禁用，需在智能体工具开关启用）：`backup_core_get_draft`、`backup_core_submit_markdown`、`backup_core_push`、`backup_core_status`。

## v0.2 计划

定时自动备份（宿主 cron + agent 转换）、备份完整性校验与一键恢复、Gitee/GitHub 双推差异报告。
