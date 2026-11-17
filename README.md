# 原型工作流插件

当前批准发行 **0.2.12（预发布）**。本版累计改进既有页面交互继承、开发阅读状态恢复、真实工作簿往返与安装执行稳定性，见[更新说明](docs/RELEASE-0.2.12.md)。公开工具与公司代码读取权限分别管理；仓库源码可见不代表可访问公司服务。

安装源固定为 `https://github.com/666qqx666-jpg/prototype-workflow-plugin` 的 `main` 分支，插件标识为 `prototype-workflow@qqx-prototype`。这是预发布版本，尚未晋级 Stable。

```sh
codex --no-daemon plugin marketplace add https://github.com/666qqx666-jpg/prototype-workflow-plugin --ref main
codex --no-daemon plugin add prototype-workflow@qqx-prototype
```

安装后调用“原型工作流初始化”，提供可信内部 company-profile 文件位置、已有个人 SSH 用户名和密钥路径。只提供路径，不发送密钥正文。成功后说“按这份 PRD 开始新需求，输出到指定目录”；继续已有需求时给出同一输出目录。

- 新需求检查维护者批准发行；旧需求绑定自己的执行器、Skill、Pack 和启动文档。
- 新版已安装但当前入口仍旧时，明确提示重载；重载后才能创建新需求绑定。
- 更新网络失败可使用已验证旧安装，并显示实际版本；同版本摘要变化或实体漂移会阻断。
- 产品尚未收敛时继续修改与累计阅读稿；交付时必须承接真实独立 Review，再运行原生 Gate。

环境范围：macOS、Python 3.12、Node.js 22、npm、SSH、锁定 Playwright/Chromium。本版安装包已完成本机缓存依赖安装、133项Core/Chromium检查及20个入口链接核对，安装时使用维护者已有SSH完成认证与list。限定导入页与阅读稿完成了真实XLSX往返；验证数据为合成数据。冷网络安装、同事环境、完整业务审查和全流程无纠偏生成尚未据此通过。

旧需求继续使用原绑定版本；如新需求返回 `reload_required`，按提示重载客户端或新建对话后再继续。维护者的本地私有候选入口不等同公开源更新。

[使用说明](docs/USE.md) · [反馈表](docs/FEEDBACK.md) · [来源与许可边界](docs/ORIGIN.md)
