---
name: enterprise-prototype-builder
description: Build an existing-system incremental prototype from one locked Team Candidate run.
---

# Enterprise Prototype Builder

当前阶段是既有系统增量原型，不进入 Spec、Plan 或开发。

1. 验证 Run Snapshot、Entry、Source Set 和锁定 Release Pair。
2. 读取 `prototype-design-workflow`，但只启用既有系统增量模式。
3. 按用途读取六类本地来源，不扩展目录范围。
4. 输出材料角色表、页面关系矩阵、现有结构保留清单、本次增量变化清单、规则呈现分层表和真实页面基线证据包。
   在生成或修改原型前，先让用户确认上述两份清单，并记录 Reader 组成；样本来源、产品范围和已验证覆盖必须分开声明。
5. `baseline-evidence` 与 `high-fidelity-readiness` 必须均为 `PASS`；Candidate 不接受 `NOT_APPLICABLE`。
6. 先区分已有原型与首次生成。已有原型源是唯一修改基线；先记录路径、修改前 Hash 和未提交状态，再做最小增量。首次生成只接受 `first-baseline-create` 锁定的确认记录，记录限定检索、用户确认无本需求旧源及唯一目标路径；不能拿空白 HTML、截图或其他需求原型充当旧源。真实产品基线及两个 PASS 门仍必须完整。读取核心 `references/local-source-and-first-generation.md` 执行本机来源及首次生成分支。
7. `light` 只生成原型；0.1.13 新 `standard` 必须生成 v4 Manifest、Evidence Bundle、累计只读 Reader Snapshot 和 metadata。Reader 是派生阅读稿，不能替代唯一可编辑原型源；revision 还必须绑定 previous Manifest、Reader Snapshot 与 lineage receipt。
8. 使用 `enterprise-prd prototype validate-output` 生成验证回执。
9. 汇总产品级变化并判定 `prototype-sync`；未授权回写时不修改 PRD。
10. 用户确认产品三件套收敛后，可选择生成只读冻结副本；Freeze 不替代 Standard、Independent Review、Final Gate 或 Stable 发布。
11. 使用 `enterprise-prd prototype builder-handoff` 生成内容无关 Builder Handoff。
12. 停止；Independent Review v2 必须在全新对话进行，且顺序晚于 Builder Handoff。

读取 `references/builder-output.md` 获取固定输出契约。

读取 Entry 绑定的冻结就绪记录，逐 pending_interactions ID 记录承接、保留依据、收敛结果与证据定位；未解决进入 pending_item_ids，不因 Entry PASS 关闭。产品级变化仍按原 prototype-sync；未授权不改 PRD。
