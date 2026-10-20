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

## v0.3 修正：本地转化层（2026-10-19 夜，用户补充）

**原则**：本地仓库不是原始文件的脱敏拷贝，而是**转化后的产物**——脱敏 → 总结 → 规定格式。格式参考 xiaoxin-memory 仓库（persona + memory-lite 编号条目 + 按日日记 + 模板规范）。

### 流程：积累 → 推送前转化 → 上云（用户修正 2026-10-19 夜）

| 阶段 | 频率 | 动作 | 模型 |
|---|---|---|---|
| 积累 | 每 2h | 采集脱敏 → 本地 commit（raw 快照累积） | 否 |
| 转化 | 每晚 push 前 | 对当天积累的 raw 做总结提炼 → 规定格式（persona/memory-lite/daily）→ commit | 是（每晚一次） |
| 上云 | 每晚 | 本地仓库镜像 push GitHub+Gitee | 否 |

模型只在每晚转化时调用一次；转化的输入是当天全部积累，输出是精炼的规定格式。

### 本地仓库布局（参考 xiaoxin-memory）

```
vault/
├── persona.md          # 提炼层：agent 人设概览
├── memory-lite.md/json # 提炼层：编号精简记忆
├── daily/YYYY-MM-DD.md # 提炼层：当日日记式总结
├── raw/                # 机械层：脱敏原始快照（溯源）
└── templates/          # 格式规范（保证提炼格式一致）
```

### 提炼层格式规范（取自 xiaoxin-memory 实践）

- memory-lite：编号连续、一条一事、用户引语保留原话、治理/事实卡类条目不被改写
- daily：第一人称叙事（agent 视角）、事实与引语逐字、不编造
- persona：分段人设概览（身份/风格/边界/称呼）
- 提炼产物经 submit_markdown 同款密钥扫描闸门后才可入仓

### 与既有三段式的关系

v0.1 的 get_draft → submit_markdown → push 三段式保留，但**插入到本地 commit 之前**：
采集脱敏 → (每日)Agent 提炼 → 本地 commit（机械层+提炼层）→ 每晚 push 远端镜像。

## v0.3 定稿补充（2026-10-19 夜）

- **云端只留精炼产物，raw 不上云**（用户拍板：避免膨胀）。
- 实现方案：raw 积累**不用 git**——`vault/raw/YYYY-MM-DD-HH/` 日期目录快照（2h 一个，保留 N 天自动修剪）；git 仓库只服务精炼层（`vault/repo/`），每晚转化写入→commit→push 远端。
  - 好处：云端零 raw、无分支复杂度、raw 修剪不影响 git 历史。
- 决策闭环清单：①paw-vault 双远端 ②内置定时器 ③全备+敏感闸门 ④key 入 working.secret ⑤连续3次失败告警 ⑥单 agent（多 agent 留 v0.4）⑦push 前统一转化 ⑧raw 不上云。

## v0.3 定稿修正：单远端（2026-10-20 晨）

- 用户修正：远端**单推 Gitee 正本**即可；GitHub 副本由 **Gitee 平台镜像自动同步**获得（先例：xiaoxin-memory 即此模式）。
- 插件 targets 只配一个 remote（Gitee）；平台侧建仓时开启镜像同步。
- 收益：插件少管一个远端、失败面更小、配置更简单；双副本效果不变。
