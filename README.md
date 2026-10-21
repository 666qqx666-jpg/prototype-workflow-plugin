# 原型工作流插件

本机预发布候选 **0.2.2**，包含 [UPD-01 更新恢复修复](docs/UPD-01.md)。公开工具与公司代码读取权限分别管理；仓库源码可见不代表可访问公司服务。

安装源固定为 `https://github.com/666qqx666-jpg/prototype-workflow-plugin` 的 `main` 分支，插件标识为 `prototype-workflow@qqx-prototype`。本候选尚未上传、公开或发布 Stable；以下正式来源命令供发布授权后使用，不表示远端已可安装。

```sh
codex --no-daemon plugin marketplace add https://github.com/666qqx666-jpg/prototype-workflow-plugin --ref main
codex --no-daemon plugin add prototype-workflow@qqx-prototype
```

安装后调用“原型工作流初始化”，提供可信内部 company-profile 文件位置、已有个人 SSH 用户名和密钥路径。只提供路径，不发送密钥正文。成功后说“按这份 PRD 开始新需求，输出到指定目录”；继续已有需求时给出同一输出目录。

- 新需求检查维护者批准发行；旧需求绑定自己的执行器、Skill、Pack 和启动文档。
- 新版已安装但当前入口仍旧时，明确提示重载；重载后才能创建新需求绑定。
- 更新网络失败可使用已验证旧安装，并显示实际版本；同版本摘要变化或实体漂移会阻断。
- 产品尚未收敛时继续修改与累计阅读稿；交付时必须承接真实独立 Review，再运行原生 Gate。

环境范围：macOS、Python 3.12、Node.js 22、npm、SSH、锁定 Playwright/Chromium。当前验证为隔离 HOME、原生 CLI、本机 Git 来源与合成 SSH；实际桌面安装和最终 GitHub 来源仍未验证。合成 Review/Gate 只验证协议链，不代表业务独立验收。

[使用说明](docs/USE.md) · [反馈表](docs/FEEDBACK.md) · [来源与许可边界](docs/ORIGIN.md)
