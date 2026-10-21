---
name: enterprise-prototype-code-query
description: Candidate 专用受限 SSH 代码查询；新 Run 前验证认证与服务 list，用户选定项目后提取最小 current-code 本地证据。
---

# Enterprise Prototype Code Query

只使用 START-HERE 中版本锁定的 `enterprise-prototype-code-query` launcher，不调用全局 `code-context-query` 或维护者源码。

1. 安装与服务 Doctor 仅验证认证和一次 `list`，scope 为 service，不证明任何项目读取权限。新 Run 在 entry-create 与 source-set-create 前执行 launcher `doctor`；失败分别输出 `baseline-evidence: BLOCK` / `restart_required`，不得以截图降级高保真 PASS。
2. 用户明确目标项目与范围后，执行 `overview PROJECT`、`search KEYWORD --project PROJECT --limit N`（1–50）、`read PROJECT RELATIVE_PATH`。
3. 未选项目时只请求唯一目标项目，不调用 overview/search/read。项目不可见、查询失败、空结果、错误对象均阻断代码基线，不形成成功 Source Set，不取消基础安装。至少精读主页面、核心组件和样式或接口定义；list PASS、命中列表或错误文本不等于真实代码证据。
4. 把项目 ID、关键词、相对路径与必要代码片段写入本 Run 的最小 `current-code` 本地来源，由现有 Source Set SHA-256 锁定。
5. Snapshot 后 Builder、Review、Gate 只验证锁定本地文件，不复查远端、不静默替换证据。权限撤销不能改写既有 Run。

服务端点、远端脚本和 known_hosts 来自已导入且由安装回执绑定的本机只读 company-profile；个人配置由 `prototype-kit.py configure-code-query` 写入私有目录。只能使用个人 SSH 身份，不复制维护者私钥，不收集密码或口令。加密密钥由用户用系统 ssh-add 解锁。

用户名、私钥路径、代码正文和原始 stderr 不进入安装 Receipt、最终 Handoff、正式 Evidence 或反馈包。复用已有用户名/密钥路径映射，不自动读取 SSH alias 或接管 ssh config；认证拒绝先核对已有配置和 ssh-agent，确实未授权才联系权限负责人。本 Skill 不生成密钥、不建账号、不登记公钥、不改 ACL，不执行通用 Shell、上传、转发或写入远端。
