---
name: prototype-workflow-entry
description: 从 PRD 开始原型需求，或继续已有需求的修改、累计阅读稿与最终交付；先锁定需求的实际运行版本。
---
# 原型工作流
当前已加载入口发行：0.2.0。

维护者明确要求“私有候选真实试跑”时，先用当前候选的 `plugin_runtime.local_trial`，显式传入候选根、版本、包摘要、新建隔离根、可信公司 profile 和已有 0600 个人 JSON 配置。该入口使用真实安装与绑定，拒绝已存在的隔离根；它不调用公共刷新、不跳过校验。仅 ready 后，按返回 home/workspace 通过下述 resume/run 继续；普通新需求仍使用 new。私有入口成功不代表桌面 marketplace 安装或公共发布通过。具体命令见维护仓库 docs/LOCAL-CANDIDATE-VALIDATION.md。

在当前候选插件根调用：`python3.12 -B -m plugin_runtime.local_trial --plugin-root <当前根> --version 0.2.0 --package-sha256 <选定包摘要> --trial-root <不存在的新目录> --company-profile <可信profile路径> --user-config <已有0600个人JSON配置路径>`。父目录须已存在；缺材料时复用已知配置，不扫描密钥正文。环境仍要求 macOS、Python 3.12 和 Node 22。

已有成功安装可通过 `--dependency-cache <安装目录>` 复用相同锁文件的依赖缓存；仍执行全部安装校验，并明确不是冷安装。

1. 从本 SKILL.md 所在目录向上两级定位插件根并调用 Python 模块 plugin_runtime.cli。CLI 的 new 会在刷新缓存前解析 workspace 并切到稳定目录，避免缓存替换使后续安装失去工作目录；无需用户手工绕行。先使用用户指定输出根检查需求绑定；已有绑定、换对话或继续修改均为 resume，不自动升级。run 的相对参数仍按调用目录解释。
2. 全新需求执行 new，传入 workspace 和本正文固定的 loaded-plugin-version=0.2.0；已有需求执行 resume，仅传 workspace。缺初始化时转 prototype-setup，不绕过。
3. reload_required 时说明目标版本、实际入口与宿主重载操作；停止业务生成，不声称已采用新版。完成重载后用新入口重新执行 new。
4. ready 后完整读取 binding.start_here_path，以及绑定安装树中的 enterprise-prototype-chain/SKILL.md；再按该入口读取需要的核心 Skill。fallback 明示实际旧版。不使用当前插件或默认链接中的业务 Skill 代替绑定实体。
5. PRD、材料或授权缺项按原工作流集中处理。产品尚未收敛时继续讨论并同步已授权 PRD、原型和唯一累计阅读稿，保留已确认内容，不在每次修改后重开独立审查。
6. 用户确认收敛后，依绑定契约准备独立 Review 启动材料；作者自审不得替代独立业务审查。承接真实独立结果后才执行原生登记及最终 Gate，输出实际覆盖范围和未覆盖事项。
7. 所有原生命令都通过 run 和本需求 workspace 调用绑定执行器。反馈只生成本地空白表，不自动上传或跨对话创建、发送任务；不恢复旧停止 Run 或套用历史兼容路径。
