# 备份核心设计（v0.1）

目标：容器/宿主随时可能重置，**重要数据必须落在容器外的私有 git 仓库**（GitHub + Gitee 双副本）。

## 仓库布局（每个远端相同）

```
paw-backups/                      ← 私有仓库
├── latest/                       ← 每次推送覆盖：当前全量快照
│   ├── manifest.json             ← 文件清单 + 时间 + 版本
│   ├── READABLE.md               ← 插件确定性渲染的可读版
│   ├── AGENT-NOTES.md            ← Agent 转换的叙事版（可选）
│   ├── companion/…               ← 脱敏后的陪伴数据
│   └── workspace/…               ← MEMORY/PROFILE/AGENTS/SOUL/jobs/技能
└── history/YYYY-MM-DD/           ← 每日一份，保留最近 30 天，自动修剪
```

## 备份内容矩阵

| 来源 | 包含 | 脱敏 |
|---|---|---|
| companion-core | state/schedule/routine/relationship/dates/notes/interactions/usage/profiles/diary/weekly/dreams/history | organizer `api_key`→`***`；care `token`/`model_signature` 剔除 |
| 工作区 | MEMORY.md、PROFILE.md、AGENTS.md、SOUL.md、jobs.json、skills/companion_core/SKILL.md（可配 glob） | 命中密钥特征的 markdown 提交被拒 |
| 永不备份 | credentials.yaml、~/.ssh/*、organizer 明文密钥、会话记录、chats.json | — |

## 节奏与触发

- v0.1：手动 / Agent 工具触发（三段式：collect → draft→markdown → push）
- v0.2：每日 03:30 宿主 cron + agent 转换；推送失败进状态记录并在检查页告警

## 恢复路径

1. companion 数据：clone 后走 companion `/import`（记录类）或整目录替换（含 setup/organizer 配置，需人工确认密钥重填）
2. 工作区文件：直接拷回
3. v0.2 加 `POST /restore?from=history/<date>` 一键校验恢复

## 双远端策略

GitHub 与 Gitee 同内容互为转载副本；任一推送失败不影响另一个，状态里分别记录。
