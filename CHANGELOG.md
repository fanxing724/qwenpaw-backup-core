# v0.1.0

- 首个版本：采集 companion 数据与工作区重要文件，脱敏后暂存；Agent 三段式转换（get_draft → submit_markdown → push）；git over SSH 推送多远端。
- 远端布局 `backups/latest/` + `backups/history/YYYY-MM-DD/`（保留 30 天自动修剪）；`repo_subdir` 支持把备份放进任意仓库子目录且不清除其他内容。
- 安全边界：organizer api_key→`***`、care token/model_signature 剔除；命中密钥特征的转换稿 400 拒收；单文件 ≤2 MiB、单次 ≤400 文件/24 MiB；SSH BatchMode 不弹凭据。
- 空仓库首推自动创建分支；无变化不产生空提交；每目标独立报错互不影响。
- 测试：file:// 裸仓库离线全链路（首推/no-change/子目录隔离/脱敏/密钥拒收/状态记录）3 用例全绿。
- 开源协议：MIT。
