# Builder 输出契约

按固定顺序输出：

1. Run 与锁定版本摘要；
2. 材料角色表；
3. 页面关系矩阵；
4. 现有结构保留清单；
5. 本次增量变化清单；
6. 规则呈现分层表；
7. baseline-evidence 与 high-fidelity-readiness；
8. 原型、Manifest、Evidence 和 v3 Independent Review 路径与 Hash；
9. prototype-sync、Pending Item ID 与 Blocker ID；
10. `enterprise-prd prototype builder-handoff` 返回的路径与 Hash。

Builder Handoff 只包含关闭契约中的 ID、路径、版本、Hash、状态、覆盖范围和短 ID 列表。不得嵌入 PRD、代码、截图、HTML、设计说明或 base64。生成 Handoff 后停止；不得在 Builder 对话代替 Reviewer。

读取 Entry 绑定的冻结就绪记录，逐 pending_interactions ID 记录承接、保留依据、收敛结果与证据定位；未解决进入 pending_item_ids，不因 Entry PASS 关闭。产品级变化仍按原 prototype-sync；未授权不改 PRD。
