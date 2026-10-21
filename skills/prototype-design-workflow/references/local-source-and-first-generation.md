# 明确授权本机来源与首次生成

这两个分支修复来源可用性，不降低 `baseline-evidence`、`high-fidelity-readiness`、Standard、独立 Review 或最终业务 Gate。工程测试通过不等于真实需求通过。

## 本机代码

仅在用户明确授权特定项目根目录及读取范围后使用；中心机失败不会自动授予本机权限。Agent 在授权范围内定位主页面、核心组件、样式、接口、状态、权限与路由，并将精确选择写入私有授权记录。记录绑定用户原话或已确认任务材料的实际路径及摘要；JSON 不能证明组织身份，来源权威仍须独立复核。禁止读取私钥、个人 SSH 配置、环境秘密或无关业务目录。

授权 JSON 的关闭结构：

```json
{
  "schema_version": 1,
  "kind": "local-code-authorization",
  "source_root": "<明确授权的项目绝对路径>",
  "files": [{"path": "<项目内相对源码路径>", "roles": ["host", "component", "style", "interface", "state", "permission", "route"]}],
  "authorized_by": "<实际授权者，不冒充用户>",
  "authorized_at": "<实际确认时间>",
  "authorization_evidence": {"path": "<已有授权证据路径>", "sha256": "<证据实际摘要>"}
}
```

示例将全部角色放在一项只是结构说明；实际必须分别读取对应文件，不能为凑覆盖虚标角色。运行包内原型 launcher 的 `prototype local-code-capture --run-root ... --entry ... --authorization ...`。结果作为 `current-code`；文件正文仍分别作为 Standard 的 code evidence，不能拿摘要清单替代精读或页面约束。来源选择允许混合已锁定 TPR 远程采集与明确授权的本机 I5：在 `current-code` 来源索引中保留每个精确捕获记录及其摘要，不能丢失任一来源；本机捕获记录进入六来源绑定时必须用生成的结构记录，不用任意转述覆盖。

混合来源用 `prototype code-source-collect --run-root ... --entry ... --captured-source <原远程采集文件> --captured-source <本机捕获记录>` 生成关闭式索引，绑定每个子源及摘要，再将该索引作为唯一 `current-code`。禁止手写无绑定的聚合摘要；不允许嵌套集合。远程子源的原项目授权和 launcher 采集证据仍须保留并独立复核。

全部原文件、授权与授权证据均写前后及跨阶段重验。漂移返回 `restart_required`，不生成新的成功产物。锁定后不替换来源。版本关系独立核实；没有证明就记录缺口，不把本机提交、采集时间或画面相似认定为同部署版本。

## 首次原型

先限定检索本需求旧源。找到唯一旧源就沿用；没找到不等于全机不存在，只问一次最小确认。用户确认尚无本需求原型后记录：

```json
{
  "schema_version": 1,
  "kind": "first-prototype-confirmation",
  "workspace_root": "<实际输出工作区绝对路径>",
  "prototype_relative_path": "docs/prototype.html",
  "no_existing_prototype_confirmed": true,
  "searched_roots": ["docs"],
  "confirmed_by": "<实际确认者>",
  "confirmed_at": "<实际时间>",
  "confirmation_evidence": {"path": "<实际确认材料路径>", "sha256": "<实际摘要>"}
}
```

运行 `prototype first-baseline-create --run-root ... --entry ... --confirmation ...`。它检查目标尚不存在，生成结构声明而不是旧 HTML；六来源的 `current-prototype-baseline` 绑定此记录。Source Set 和启动时再次检查目标不存在；生成后只允许该唯一路径。不能覆盖既有源、使用别的需求原型或绕过真实页面来源。

Standard 的 `prototype.baselineKind="first-generation"`；`baselineSourceId` 与 `baselineHash` 绑定该声明，evidence source 的类型仍为 `prototype-baseline`、contentKind 为 `text`。runtime、code、同版本截图均必需，独立复核强制触发 `first-real-use`。已有源使用 `existing-prototype` 或原默认行为，仍绑定修改前原文件快照。

对隐藏、未上线或不可达页面先核对代码与实际页面关系。不凭未走查断言不存在，也不擅自启用新入口；业务接续、重算和守恒仍留在 PRD/系统验收，不用虚构的订单页证明通过。
