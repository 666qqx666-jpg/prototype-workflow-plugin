# Prototype Quality Baseline

- 既有系统增量必须同时锁定 current-product-requirement、confirmed-product-decision、observed-runtime、current-code、same-version-screenshot、current-prototype-baseline。
- 当前工作区原型是唯一业务修改基线；派生阅读器和快照不得替代它。
- 页面结构、组件、尺寸、间距、文案和交互继承可验证的当前页面；新增能力只做需求要求的最小插入。
- 产品事实只来自已审 PRD 或确认裁决；页面现状只来自真实页面、当前代码和同版本截图。
- 用户可见说明与内部实现规则分层；接口、记录生成和后端校验不自动成为页面文案。
- light 交付 HTML 与基础验收；0.1.19 Standard 交付 v4 Manifest、Evidence Bundle、累计只读 Reader Snapshot 与 metadata。revision 另绑定前版 Manifest、Reader Snapshot 和 lineage receipt。
- Reader 组成可以继承历史阅读稿，但业务原型源始终唯一；事实来源详情分别独立折叠。
- 产品范围、样本来源与已验证覆盖是不同声明；样本不能代替范围证据。
- 卡片身份保持稳定，显示序号必须连续为 1..N；已解决 pending 不得继续显示，未解决项不得假关闭。
- Builder Handoff 后必须在新上下文完成 Independent Review v2，逐项复核 scope、pending 与六类负向检查，再通过 Final Gate。
- 产品三件套冻结为可选的只读历史快照；冻结本身不代表 Independent Review、Final Gate 或 Stable 发布通过。
- 产品级变化必须使 prototype-sync=block，直到获得授权并完成同步；纯呈现调整可为 pass。
