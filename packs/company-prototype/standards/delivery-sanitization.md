# Delivery Sanitization

Release ZIP、公开快照和正式 Fixture Evidence 禁止包含个人绝对路径、个人知识库、凭证、真实客户数据、动态 PRD 正文、本地 Evidence 正文、node_modules、test-results 和构建临时文件。运行 Handoff 只保存内容无关的路径、Hash、版本、状态和短 ID；本机安装 Receipt 不进入 Git 或发布包。
