---
name: prototype-workflow-entry
description: 从 PRD 开始原型需求，或继续已有需求的修改、累计阅读稿与最终交付；先锁定需求的实际运行版本。
---
# 原型工作流
当前已加载入口发行：0.2.2。
1. 从本 SKILL.md 所在目录向上两级定位插件根，在该目录调用 Python 模块 plugin_runtime.cli。先使用用户指定输出根检查需求绑定；已有绑定、换对话或继续修改均为 resume，不自动升级。
2. 全新需求执行 new，传入 workspace 和本正文固定的 loaded-plugin-version=0.2.2；已有需求执行 resume，仅传 workspace。缺初始化时转 prototype-setup，不绕过。
3. reload_required 时说明目标版本、实际入口与宿主重载操作；停止业务生成，不声称已采用新版。完成重载后用新入口重新执行 new。
4. ready 后完整读取 binding.start_here_path，以及绑定安装树中的 enterprise-prototype-chain/SKILL.md；再按该入口读取需要的核心 Skill。fallback 明示实际旧版。不使用当前插件或默认链接中的业务 Skill 代替绑定实体。
5. PRD、材料或授权缺项按原工作流集中处理。产品尚未收敛时继续讨论并同步已授权 PRD、原型和唯一累计阅读稿，保留已确认内容，不在每次修改后重开独立审查。
6. 用户确认收敛后，依绑定契约准备独立 Review 启动材料；作者自审不得替代独立业务审查。承接真实独立结果后才执行原生登记及最终 Gate，输出实际覆盖范围和未覆盖事项。
7. 所有原生命令都通过 run 和本需求 workspace 调用绑定执行器。反馈只生成本地空白表，不自动上传或跨对话创建、发送任务；不恢复旧停止 Run 或套用历史兼容路径。
