# Prototype Review 输出契约

Review 必须在新的 Context ID 中运行。机器输出只能使用：

- schema_version
- prototype_run_id
- review_context_id
- reviewed_builder_handoff_sha256
- reviewed_prototype_sha256
- verdict
- findings
- reviewed_at

每个 finding 只含 severity、code、message、artifact_ref。纯审美偏好只能是 note，不能是 blocker。人类说明按“结论、阻断项、主要问题、非阻断建议、已重验 Hash”排序。Review 不修改原型、PRD 或 Builder Handoff。

独立读取 Entry 绑定的冻结就绪记录，对照全部交互待办 ID 的最终去向，核对未承接、假关闭和未授权产品事实；有此缺陷须 BLOCK。不能把上游 self_review 写成独立 PRD Review，不能以审美或其他未触发模式加 Gate。
