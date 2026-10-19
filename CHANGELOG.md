# v0.2.0

- 两级备份架构：第一级本地仓库高频提交（kind=local，无网络依赖，建议 1-2 小时一次）；第二级本地仓库镜像推送远端（kind=remote，GitHub/Gitee 多远端，建议每晚一次）。
- 新增 POST /commit-local 端点与 backup_core_commit_local 工具（采集+脱敏+本地提交一步完成）。
- Target 模型新增 kind（local/remote）与 path 字段；remote 推送改为以本地仓库为源镜像推送，不再每次 clone 缓存。
- 新增 agent_id 绑定：设置绑定智能体后只备份该 agent 的工作区，避免备错对象。
- 测试更新为两级流程（本地提交→远端推送）。

# v0.1.0

- 首个版本：采集 companion 数据与工作区重要文件，脱敏后暂存；Agent 三段式转换（get_draft → submit_markdown → push）；git over SSH 推送多远端。
- 远端布局 `backups/latest/` + `backups/history/YYYY-MM-DD/`（保留 30 天自动修剪）；`repo_subdir` 支持把备份放进任意仓库子目录且不清除其他内容。
- 安全边界：organizer api_key→`***`、care token/model_signature 剔除；命中密钥特征的转换稿 400 拒收；单文件 ≤2 MiB、单次 ≤400 文件/24 MiB；SSH BatchMode 不弹凭据。
- 空仓库首推自动创建分支；无变化不产生空提交；每目标独立报错互不影响。
- 测试：file:// 裸仓库离线全链路（首推/no-change/子目录隔离/脱敏/密钥拒收/状态记录）3 用例全绿。
- 开源协议：MIT。
