# 私有候选验证

## 真实私有试跑入口

私有候选通过独立的 `plugin_runtime.local_trial` 初始化并建立真实绑定，普通公共 `new` 不改变。维护者选定包后，在该候选插件根执行：

```sh
python3.12 -B -m plugin_runtime.local_trial \
  --plugin-root /absolute/candidate/plugins/prototype-workflow \
  --version 0.2.6 \
  --package-sha256 <选定工具包的64位摘要> \
  --trial-root /absolute/existing-parent/new-trial \
  --company-profile /absolute/trusted/profile.json \
  --user-config /absolute/existing/private/user.json
```

`trial-root` 必须不存在，父目录须已存在，不能在插件目录内或经过符号链接。入口独占创建其中的 home 与 workspace，绝不接管已有 HOME；个人 user.json 必须是已有的 0600 配置，只引用密钥路径，不交付或读取密钥正文。调用前确保 Python 3.12、Node 22 在本进程 PATH 中。

可选 `--dependency-cache <已有成功安装目录>` 只复用相同 package-lock 的 npm／Chromium 缓存；拒绝向缓存外逃逸的链接。完整依赖恢复、安装 Smoke 与 SSH 校验仍执行，成功记录明确标注缓存使用，不宣称冷安装通过。

入口核对版本及选定摘要，运行真实依赖恢复、安装 Smoke、个人 SSH 认证与 list，成功后复用正式绑定创建和复验。失败不产生成功绑定或成功结果，现场留在新目录；失败后修正原因并换新目录重试。版本、来源或内容改变都不能静默采用另一包。

ready 后使用返回的 home/workspace，通过**同一候选**的 `plugin_runtime.cli resume/run` 执行后续步骤，读取真实绑定的 START-HERE 与核心 Skill。不要对该需求再运行会刷新公共来源的 new。这证明私有工作流入口，不等同 Codex 桌面 marketplace 安装、公共发布或业务生成通过。

## 离线检查入口（不能替代真实试跑）

维护者修改 Skill 后先构建到新的隔离目录，不覆盖既有发行、安装树或需求绑定：

```sh
python3 -B tools/build_public_plugin.py --version 0.2.6 --output /tmp/prototype-build-unique
python3 -B tools/verify_local_candidate.py --plugin-root /tmp/prototype-build-unique/plugins/prototype-workflow --output /tmp/prototype-check-unique
```

目录必须未存在；示例版本只是本地开发标识，不表示已公开或批准更新。工具复制实际候选到临时目录，从该副本加载 CLI/manager，并校验包摘要、归档树、普通 Python 导入、缓存刷新目录、重载、旧需求锁定和失败关闭。输出明确记录真实执行与模拟部分。它不调用安装器、SSH、浏览器或远端服务，不生成业务成功交付。

生产 `new` 继续只刷新批准的公共来源；不要用它验证尚未发布的私有候选，也不要改来源或手工制造需求绑定来冒充正式入口通过。真实私有试跑使用上节独立入口；本节测试 Backend 的注入只存在于测试代码，没有跳过校验的生产开关。

路由测试的需求绑定由 FakeBackend 产生，只证明入口选择和锁版分支，不证明生产安装树或正式绑定合同通过。真实归档校验与这些模拟路由分别记录。

语义与交互的行为试做使用核心 Skill 的 `evals/fixtures/interaction-fidelity/request.md` 及其原始材料。评估者不读取 evals.json 的预期答案或检查脚本，输出到工作树外；随后用 `node tests/check_fidelity_output.mjs <输出目录>` 检查模型与卡片结构，并人工核对业务语义。模型通过不能替代实际 HTML、复杂组件视觉和最终累计阅读入口的浏览器检查。

新的真实需求验证仍应在获准的来源与环境中经过初始化、新需求绑定、生成、增量和交付。无旧原稿首次生成只读取批准材料，旧正式原型留作事后对照；维护者临时绕行和手工修补须记录。未运行的实际桌面安装、冷网络依赖恢复、真实 SSH、浏览器 Runtime Audit 与业务 Review/Gate 都保持未验证，不从离线测试推导通过。
