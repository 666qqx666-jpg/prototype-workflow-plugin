---
name: enterprise-prototype-chain
description: Start and route the Enterprise Prototype Team Candidate from an approved PRD review and locked local evidence.
---

# Enterprise Prototype Chain

1. 当前只支持 `existing-system-increment`；0→1、半参考型和完全重设计一律停止并说明 Candidate 边界。
2. 安装和服务 Doctor 只验证 SSH 认证及一次 `list`，不验证项目读取。新 Run 在 `entry-create` 与 `source-set-create` 前必须调用 START-HERE 指定的 `enterprise-prototype-code-query doctor` launcher；失败分别输出 `baseline-evidence: BLOCK` / `restart_required`，不得创建成功 Source Set。Entry 前只读用户已指定的当前 PRD 与适用审查材料，核对对象、权限、核心处理/异常结果及审查范围；不自动读取其引用、其他目录或页面。
生成本 Run 的 prototype-readiness-assessment.json，明确 review_scope、reason、阻断项和逐条交互待办，摘要绑定实际 PRD/审查。NOT_ASSESSED 只留档，不能自动生成 prototype PASS；核心产品事实缺口仍 BLOCK。
用 --readiness-assessment 创建 Entry v2 的 prototype-entry.json，失败不创建成功 Entry/Source Set。旧 v1 Run 不迁移，不能现场修改安装包。Entry PASS 只允许进入材料采集，不代表高保真基线通过。
3. 用户确认精确项目与代码范围后，依次执行 `overview`、`search`、最小 `read`，取得主页面、核心组件和样式或接口定义，把项目 ID、查询词、相对路径和必要片段写入本 Run 的 `current-code` 本地来源文件，再 `source-set-create` 和 `start`。未选项目只请求唯一目标项目，不执行项目查询。项目不可见、查询失败、空结果或错误对象均阻断代码基线（BLOCK/restart_required），不生成成功来源，不取消基础安装；list PASS 不得冒充代码证据。
4. `start` 成功后只使用 Snapshot 锁定的六类本地来源；Builder、Review 与 Gate 不因远端变化重查或替换代码证据。SSH 被撤销不改写已锁定 Run。
5. 团队 Candidate 只接受 `ready` Snapshot；`degraded`、`blocked` 和 `restart_required` 均阻断。
6. 只使用 Snapshot 锁定的 Skill、Pack、Entry 和 Source Set。调用 `enterprise-prototype-builder`；Builder 必须再读取 `prototype-design-workflow`。
7. 0.1.13 新 Standard Run 必须使用 Manifest v4.0.0。revision 必须绑定 previous Manifest、Reader Snapshot 和 lineage receipt；旧 v2/v3 只作显式历史基线。
8. Builder 输出累计 Reader Snapshot 与 metadata，保留稳定 card ID 并显示连续序号；Reader 不取代唯一可编辑原型源。完成 prototype-sync 和用户收敛后，可选生成产品三件套只读冻结；冻结不代表 Review 或 Gate PASS。
9. Builder Handoff 完成后，在全新对话运行 Independent Review v2，再生成 Review 成功回执并调用 Gate。顺序固定为 Builder → Review v2 → Gate。
10. Review PASS 后调用 Gate；Gate 写出最终 Prototype Handoff 并返回 `status: pass` 时，才具备申请下一阶段的技术资格。
11. Gate PASS 不授权 Spec、Plan、开发、PRD 回写、飞书同步或 Stable 发布。

不得读取个人 Obsidian、未列入 Source Set 的文件或其他 Run 的上下文。来源锁定前，仅在用户精确授权的项目范围通过版本化查询 launcher 采集最小代码证据；不得自行访问其他网络来源。
