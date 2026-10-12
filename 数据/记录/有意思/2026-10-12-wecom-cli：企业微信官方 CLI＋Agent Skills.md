# wecom-cli：企业微信官方 CLI＋Agent Skills

## 保存内容

松海原话：这个到时候可以试一下。

要点（公众号「Java基基」文章《Star 3.1k 企业微信官方开源 AI Agent》，2026-10-12）：
- 企业微信官方团队（WecomTeam）开源的命令行工具 wecom-cli：让人类和 AI Agent 都能在终端中操作企业微信，把重复动作交给命令和 Agent
- 覆盖十二类业务：消息、邮件、文档、文档管理、在线表格、智能表格、智能文档、待办、日程、会议、微盘、通讯录（发消息／建文档／查人／排会／建待办等）
- 关键分层：命令是一层，Skill 是另一层——仓库内置 14 个 Agent Skills，装完 CLI 后由 Skill 层驱动（如 wecomcli-shared 会在任何命令前先查安装、版本和授权）
- 安装四条命令：`npm install -g @wecom/cli` → `npx skills add WeComTeam/wecom-cli -y -g` → `wecom-cli auth init`（默认扫码）→ `wecom-cli auth show`；Node.js 18+；凭证加密落在 `~/.config/wecom/credentials.enc`
- 核实（2026-10-12）：GitHub 仓库与 npm 包 @wecom/cli（v1.3.4，"The official CLI for WeCom"，周下载约 1.7 万）均真实存在；仓库 MIT，3,173 Star / 217 Fork

## 保存动机

2026-10-12 松海在飞书发来文章《Star 3.1k 企业微信官方开源 AI Agent》，说「这个到时候可以试一下」。仓库：https://github.com/WecomTeam/wecom-cli ｜ 文章：https://mp.weixin.qq.com/s/Q1SVp1OQ-GTSDeh0q414Ew
