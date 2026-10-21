---
name: enterprise-prototype-review
description: Cold-start review one Enterprise Prototype Team Candidate handoff.
---

# Enterprise Prototype Review

本 Skill 必须在全新对话运行。

1. 只接收 Builder Handoff、锁定 Snapshot、Source Set、Independent Review v2 报告和新的 Review Context ID；Review 必须发生在 Builder Handoff 之后。
2. 重新校验 PRD、页面、代码、截图、当前原型和验证回执 Hash。
3. 独立核对页面关系、现有结构保留、增量边界、产品语义来源和 `prototype-sync`。
4. `standard` 额外核对 v4 Evidence、累计 Reader、真实 Surface / Anchor、双向定位、可见连线和同一活页面两轮恢复；复核范围证据、连续显示序号、pending 闭合及历史沿革绑定。
5. 审美偏好、未触发模式和纯呈现建议不得成为 Blocker。
6. 输出 `references/review-output.md` 的封闭 JSON 和人类说明。
   Independent Review v2 必须绑定当前 Manifest、Prototype、Evidence、Reader 与 previous lineage 摘要，检查范围与 pending，并完整覆盖六类负向检查；不得复用 Builder 对 pending 语义的结论。
7. Review PASS 后使用 `enterprise-prd prototype gate` 重验并生成最终 Prototype Handoff；不自行宣布进入下一阶段。

不得继承 Builder 完整对话、读取个人 Obsidian、修改原型或写回 PRD。

独立读取 Entry 绑定的冻结就绪记录，对照全部交互待办 ID 的最终去向，核对未承接、假关闭和未授权产品事实；有此缺陷须 BLOCK。不能把上游 self_review 写成独立 PRD Review，不能以审美或其他未触发模式加 Gate。
