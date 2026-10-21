<!-- fanxing-style:start -->
<picture>
  <source media="(prefers-color-scheme: light)" srcset="https://fanxing724.github.io/new-make/fanxing724/projects/qwenpaw-backup-core/cover.light.svg">
  <img alt="Backup Core · 留一份回程" src="https://fanxing724.github.io/new-make/fanxing724/projects/qwenpaw-backup-core/cover.svg" width="100%">
</picture>

**继续折腾之前，先给数据留个落脚点。**

配置和记录会随着使用慢慢积累。这个 QwenPaw 备份项目围绕 Git 保存这些变化，让整理、迁移和后续恢复有一个可以追溯的起点。

<sub>番星 / 星轨手记 · <a href="https://fanxing724.github.io/new-make/fanxing724/projects/style.html">风格样板</a> · <a href="https://fanxing724.github.io/">个人小站</a></sub>
<!-- fanxing-style:end -->

# 备份核心（backup-core）

> 我永远不会忘记你。

[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

QwenPaw 插件：把重要文件备份到 **Gitee / GitHub 私有仓库**——插件采集并脱敏，Agent 转换为可读格式，git over SSH 推送。

> 初始想法：容器会重置、磁盘会坏，但**记忆不该丢**。

## 流程（Agent 三段式）

```
collect（采集+脱敏+可读版）→ get_draft → Agent 转换 → submit_markdown → push（双远端）
```

## 仓库内

- `plugin.py` / `plugin.json` — 插件本体（v0.1.0）
- `DESIGN.md` — 初始设计：远端布局（latest/ + history/ 留 30 天）、备份矩阵、脱敏边界、恢复路径、双远端策略
- `docs-usage.md` — 端点与 Agent 工具清单、配置示例
- `tests/test_backup_core.py` — 离线全链路测试（file:// 裸仓库当远端）

## 安全边界

密钥永不上传（organizer api_key→`***`、care token 剔除）；命中密钥特征的转换稿拒收；只推显式配置的远端；SSH BatchMode。

## 安装

```bash
qwenpaw plugin install /path/to/qwenpaw-backup-core
```

## 两级备份架构（v0.2.0）

| 级别 | 频率 | 动作 | 依赖 |
|---|---|---|---|
| 第一级：本地仓库 | 每 1-2 小时 | 采集 → 脱敏 → 本地 git commit | 无网络、无凭据 |
| 第二级：远端镜像 | 每晚一次 | 本地仓库 push 到 GitHub + Gitee | SSH key（持久化在 NAS） |

- 本地 target：`{"name":"local","kind":"local","path":"<工作区内仓库目录>"}`
- 远端 target：`{"name":"github","kind":"remote","url":"git@github.com:.../paw-vault.git"}`
- 绑定智能体：`agent_id` 设置后只备份该 agent 的工作区，避免备错对象
- 端点：`POST /commit-local`（第一级）、`POST /push`（第二级）；工具：`backup_core_commit_local` / `backup_core_push`
- 远端 push 失败不影响本地备份，下次自动重试


## 开源协议

MIT © 2026 番星。见 [LICENSE](LICENSE)。

## 本地测试

```bash
python3 -m pytest tests -q        # 离线全链路：file:// 裸仓库当远端，无需网络与凭据
```
