# 备份核心（backup-core）

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
