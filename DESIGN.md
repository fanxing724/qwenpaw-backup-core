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

## v0.3 规划：六个决策点推荐（2026-10-19 夜，待用户拍板）

### 1. 远端建仓 → 新建 paw-vault（GitHub 私有 + Gitee 私有镜像）
- 不复用 qa-sauce：职责不同（qa-sauce=精简记忆备份，paw-vault=全量脱敏备份+history）
- 双远端互为冗余；建仓需用户网页操作（GitHub API 限流中）

### 2. 调度 → 插件内置定时器（修正：原倾向宿主 cron）
- 事实核查：QwenPaw cron 的 task_type 仅 text/agent 两种，无 HTTP/shell 直调
- agent 任务跑备份=每次耗 token 且受模型限流影响（2026-10-19 实测 429），高频不可接受
- 内置 asyncio 定时器：local 每 local_interval_minutes（默认120）；push 每晚 push_time（默认23:30）
- 插件 load 时按 settings 启动；settings 变更热重启定时器

### 3. 备份范围 → 全备 + 逐文件敏感扫描闸门
- 现状脱敏只挡 key 模式（api_key/token/...），挡不住"笔记里写了服务器密码"这类叙事敏感值
- v0.3：commit 前逐文件扫描扩展模式（中文"密码/口令"、IP:端口、root@ 等），命中→该文件跳过+记入 status.skipped（不整批失败），用户可审
- 默认 globs 保持；digest/ 与 memory/ 全量入备

### 4. SSH key 持久化 → 移入 working.secret（NAS）+ symlink 回 ~/.ssh
- 先例：working.secret 已是 NAS 软链的加密密钥仓库（.master_key 600）
- 私钥入该仓 + symlink；post_reset_setup 恢复顺序加"重建 symlink"一步

### 5. 失败告警 → 连续 3 次失败才主动消息，单次静默记 status
- 符合"有异常才汇报"偏好；避免网络抖动刷屏
- 告警走绑定 agent 的 channel（复用 companion-core 关怀投递通道思路）

### 6. 绑定范围 → v0.2 单 agent_id 保持；多 agent 为 v0.4 扩展
- 扩展路径：settings.targets 每项加可选 agent_id（per-agent 采集+per-agent 本地仓）
- 当前月欣羽/default 暂无备份需求，不提前复杂化
