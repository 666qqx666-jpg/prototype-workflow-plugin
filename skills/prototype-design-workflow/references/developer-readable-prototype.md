# 开发可读型原型交付

## 0.1.19 / Manifest v4 关闭式合同

当前 Standard 新 Run 必须使用 Manifest `4.0.0`；v2/v3 只用于历史只读或明确的沿革基线，不可作为新 Standard 成功产物。使用 `assets/change-manifest-template.json` 后，先按最终视觉顺序把 `displayNumber` 重排为字符串 `1..N`，`cardId` 保持稳定。`globalRules` 不编号。

每个 v4 Run 都要独立填写 `scopeEvidence.productScope`、`sampleSource` 与 `verifiedCoverage`。产品范围只引用当前 PRD 或确认的产品裁决；样本只记录取样环境和来源，不决定产品范围；验证覆盖引用实际运行、代码或同版本截图，并分别列明未验证分支。

首次累计阅读稿使用 `delivery.lineage.mode=initial`，所有当前卡片进入 `addedCardIds`。修订使用 `revision`，提供上一版 Manifest、累计 Snapshot 和 lineage baseline receipt；四类卡片沿革互斥且完整，保留卡仅允许重新编号。旧 v3 或自定义累计稿须先运行 `scripts/create-lineage-baseline-receipt.mjs`，绑定实际输入字节和运行审计。每个旧 pending 必须归入 carried、resolved 或 superseded；本轮新增项进入 new。当前 pending 必须为 unresolved，并记录本轮对当前权威来源的定位、结论和理由。机器只检查闭合关系，产品语义由 Independent Review v2 复核。

冻结累计稿迁移允许 `--prd` 指向用户明确选定的历史副本，不要求它仍占用 Freeze 记录的旧权威物理路径。调用方必须同时提供 `--prd-version` 与 `--prd-sha256`；迁移器在任何 Run 写入前核对实际字节摘要，并在 `frozen-cumulative-reader-migration/1.1.0` 描述符中记录选定版本、选定路径、预期/实际摘要，以及 Freeze 中原权威路径、归档路径和历史摘要。路径分离只解决正式 PRD 已前进后的历史读取，不放宽旧 Manifest、Reader、metadata、Freeze、继承引用或页面/卡片范围校验。`1.0.0` 描述符仍可作为历史只读输入。

Reader 的事实来源默认独立收起，按 `card:<cardId>` 或 `global:<ruleId>` 保存展开状态。展开卡片、聚焦、定位、返回、面板收起和重绘不得改变来源状态。

Independent Review `2.0.0` 必须绑定当前 Manifest、业务原型、Evidence、累计 Snapshot 和（修订时）上一版实体摘要，并逐项记录六类负向检查。只有全项 pass、scope/pending 复核 pass 且 verdict 为 pass 才能生成成功回执；block Review 可保存为失败证据。

用户确认产品收敛后，可运行 `scripts/freeze-product-convergence.mjs` 冻结 PRD、唯一业务原型和唯一累计阅读稿。收敛记录必须由当前 Run 生成并绑定实际摘要、本机验证、无阻断 pending 和用户确认来源；显式传入 `--frozen-at` 与新输出目录。Freeze 是只读历史快照，不替代 Review 或 Final Gate，也不改变三个活跃源。

本参考用于把已确认的 PRD 与真实原型组织成开发可以并排阅读的交付物。它不负责重新设计原型，也不取代 PRD、技术方案或接口文档。

## 目录

- [1. 目标与边界](#1-目标与边界)
- [2. light / standard 选择](#2-light--standard-选择)
- [3. Manifest 信息模型](#3-manifest-信息模型)
- [4. 右卡信息顺序](#4-右卡信息顺序)
- [5. 显式界面容器](#5-显式界面容器)
- [6. 双向定位与返回](#6-双向定位与返回)
- [7. 构建、自动校验和失败语义](#7-构建自动校验和失败语义)
- [8. 当前整合版与需求快照](#8-当前整合版与需求快照)
- [9. 可选的人类观察](#9-可选的人类观察)

## 1. 目标与边界

目标是让开发在真实页面旁边看懂：

- 本次改动结论。
- 页面上的关键字段。
- 业务规则与前置状态。
- 用户动作、页面反馈和结果。
- 异常反馈。
- 可定位、可校验的事实来源。

开发理解需求是目标。5/20 分钟只是理想观察窗口，不作为门控，也不要求真人复测、反馈台账或某个人工通过状态之后才能继续候选制作与发布。

以下内容默认不进入右卡：

- 接口、请求参数、响应字段。
- 组件、模块或代码文件映射。
- 数据库、任务、队列或内部执行链路。
- 没有事实来源的实现建议。

接口映射默认不展示。只有用户明确要求，并且技术事实来源已经闭合时，才另行生成实现映射；不要把它混入产品字段和交互说明。

既有原型增量中，当前工作区的现有原型是唯一业务源。先确认并保护它的当前状态，
再在该源文件上做最小增量；禁止新建一个“更干净”的替代页面后把它当成最终
原型。右侧阅读器、整合 HTML 和截图都只是派生物，不能反向成为下轮修改基线。

交付构建层必须只读：

- 构建过程不再修改已经完成本轮增量的原型源文件和 PRD 源文件。
- 构建产物内嵌原型快照，不依赖本机绝对路径。
- 页面交互发生在快照中，不回写源文件或业务系统。

## 2. light / standard 选择

### light

使用 light：

- 用户只要原型本身。
- 已确认 plan 已写清页面、状态、尺寸和交互。
- 当前任务走快速通道，且不需要给开发补充旁路说明。

light 下直接生成并验收原型，不强制创建 manifest、右卡、编号、连线或开发阅读器。

### standard

使用 standard：

- 用户明确要“给开发看”或“交付开发”。
- PRD 与原型距离较远，字段、规则和交互需要贴着页面说明。
- 多个页面、抽屉或弹窗共享需求卡，需要可验证的定位关系。
- 页面改动包含新增、调整、删除或关键复用，需要统一范围说明。

standard 下以一份 change manifest 作为页面标记、右卡和自动校验的单一事实源，
但 manifest 不能成为自己的事实来源。每个语义结论必须绑定到本地证据包中可
校验 Hash、定位符和摘录的 PRD、用户裁决、真实页面走查或当前代码。

如果用户只要求 light，不要因为存在本参考就扩大流程。

既有系统增量在生成 Standard 前必须完成基线链：

1. 实际走查当前可交互页面，确认入口、最上层活动容器和动态状态。
2. 对当前代码做定向检索，定位宿主页面、目标组件和状态处理；代码明确不可用时
   记录原因，不能写“已参考代码”。
3. 冻结与页面 / 代码同版本的截图，作为视觉与位置基线。

代码可得却未检索、只有截图却需要判断动态交互、三类来源版本不一致或冲突未
裁决时，`baseline-evidence: BLOCK`。Skill 内部直接 fail closed，不额外让用户
确认是否带病继续。

## 3. Manifest 信息模型

0.1.19 新建 standard 交付使用 `schemaVersion=4.0.0`，顶层包含：

- `requirement`：需求标识、标题、覆盖范围和主事实源。
- `prototype`：当前工作区原型的来源摘要，包含相对 workspace root 的
  `sourcePath`、修改前 `baselineSourceId / baselineHash` 和修改后 `sourceHash`；
  `sourceRole` 固定为 `current-workspace-prototype`，但该字符串不能单独证明身份。
- `delivery`：固定为 standard + derived-readonly，明确右侧阅读器只是派生层。
- `baselineEvidence`：真实页面、当前代码、同版本截图的可用性、来源和冲突。
- `evidenceSources`：证据 ID、类型、逻辑名称、内容类型和 SHA-256。
- `pages`：本次交付涉及的页面分组。
- `surfaces`：显式界面容器。
- `globalRules`：明确不绑定页面位置的跨页面规则。
- `cards`：变化卡片。

v2/v3 只保留历史读取或显式 lineage 基线能力；validator 仅可按对应显式兼容入口
读取历史材料，不得据此宣布新的 Standard 交付 ready。v3 历史 Manifest 必须先创建
lineage baseline receipt，再用于新 Run 的 revision 绑定。

### 事实源与语义门

`requirement.primarySourceId` 必须指向 `prd` 或 `decision` 类型的真实证据。每个
evidence source 同时声明与类型匹配的 authority；evidence bundle 再记录同一
`sourceType`、原始 locator 和本地路径。validator 只允许读取显式
`--evidence-root` 下的文件，并验证 Manifest 与 bundle 的类型、Hash、定位和摘录
一致。自动门仍不能认证“这份文档在组织中是否真有最终权威”，来源权威不明确时
必须进入独立复核，不能把技术校验写成业务真值证明。

`--workspace-root` 必须取任务开始时发现的真实项目根；`--evidence-root` 只能取用户
给定材料、现有项目材料目录或可追溯任务记录所在目录。禁止为了让 validator PASS
而复制任意文本到临时材料目录、扩大到用户主目录 / 技能目录，或把 Skill、实现
说明、原型文案重标为 PRD / decision。
每张卡的以下语义单元都必须有独立 `evidenceBindings`：

- `title`、`summary`、`changeContent`。
- 每个 `fields[i]`、`rules[i]`、`interactions[i]`、`errors[i]`。

每个绑定用 JSON Pointer 风格的 `claimPath` 指向 manifest 结论，并至少引用一条
当前 PRD 或已确认产品裁决；引用必须包含 `sourceId`、确定性 `locator` 和能在该
来源对应区段解析的 `excerpt`。runtime / code 只补现状、位置和继承约束，不能
单独授权本轮目标语义；截图和原型基线只能证明视觉或位置，不能证明产品语义。

自动语义门能证明“结论已绑定到确实存在的原文位置”，也能拦截默认占位文案、
整卡复制和未解析引用；它不能证明模型对原文的理解必然正确。因此不得把门禁
命名为“语义正确性证明”。首次真实使用、规则变化、复杂跨页面、证据缺口或冲突、自动
降级或同类失败复现时，必须增加独立复核；普通交付使用完整自动门加抽样检查。

校验器会把 manifest 的显式风险、未能由 Git HEAD 证明为任务开始前既有内容的权威
来源 / 原型基线、全局规则分类、多 surface 和 full coverage 合并为必需复核触发项。
命中任一触发项时，独立复核方基于最终三份输入产出 review record，记录三个精确
SHA-256、全部触发项和 pass 结论，并通过 `--review` 传入。制作方不得自行生成这份
记录；校验器只验证对象和触发项，无法凭 JSON 字段认证真实的组织身份。

PRD 为了评审而缩短交互说明时，不得从原型制作过程反推 Standard 语义。先用
现行 PRD / 用户裁决确定目标，再用真实页面和当前代码补现状与交互基线；仍无法
证明的内容进入 `pendingItems`，并用 `blocksDelivery=true` 阻断完整交付。

### 覆盖范围

`requirement.coverage` 只能是：

- `pilot-slice`：代表性切片，不是完整需求。
- `full`：页面、卡片、来源和未决项已闭合的完整需求快照。

当 coverage 为 `pilot-slice` 时，总览必须说明：

- included：当前明确覆盖的页面和卡片。
- navigation-only：只用于导航或打开共享容器的页面。
- out-of-slice：PRD 中存在但本快照没有展开的内容。

统一写入 `requirement.scope.included`、`requirement.scope.navigationOnly` 和
`requirement.scope.outOfSlice`；不要由执行者临时发明其他字段名。

不得把代表性切片命名为“完整需求”。

### 页面

每个 page 至少包含：

- `pageId`
- `title`
- `order`

page 是阅读分组，不等于活动界面容器。一个页面可以包含 view、drawer 和 modal 等多个 surface。

### 卡片

每张 card 至少包含：

- 标识：`cardId`、`displayNumber`、`requirementId`。
- 归属：`pageId`、`surfaceId`、`region`。
- 结论：`title`、`summary`、`changeKinds`、`changeContent`。
- 说明：`fields`、`rules`、`interactions`、`errors`。
- 追溯：`evidenceBindings`。
- 定位：`anchor`。
- 状态：`pendingItems`、`history`。

change kind 只使用：

- `add`
- `modify`
- `delete`
- `critical-reuse`

新增卡只写新增内容、位置和最终结果，不写“原来是什么 / 现在是什么”。

调整与删除放在同一张卡中，呈现保留、调整、移除和最终页面结果；不要把同一处页面改造拆成互相割裂的两张卡。

关键复用要明确：

- 复用的界面容器。
- 覆盖的业务入口。
- 不允许改变的结构。
- 本次只新增或调整的能力。

### 全局规则

无法归属到单一页面元素的跨页面权限、统一口径或共用约束进入 `globalRules`。
它们必须有事实来源，但不得拥有 `pageId`、`surfaceId`、`displayNumber` 或
`anchor`，也不得生成编号、高亮和连线。每条规则必须用 `appliesToPageIds` 明确至少
两个适用页面和非定位原因；含按钮、入口、字段、点击、选择、打开抽屉 / 弹窗等位置
或动作语义时自动失败。能在页面中定位的规则仍应落入对应 card，不能为了逃避锚点
验收而放入全局规则。

除标题、摘要和每条规则外，`scope`、`appliesToPageIds`、`nonLocatableReason` 也必须
分别绑定权威事实源；对应摘录必须明确出现“全局 / 所有页面 / 跨页面 / 统一适用”等
范围语义。存在全局规则时自动触发独立复核，避免只靠关键词门把错误分类变成假 PASS。

## 4. 右卡信息顺序

右卡固定按以下顺序组织：

1. 改动结论。
2. 关键字段。
3. 业务规则。
4. 交互说明。
5. 异常反馈。
6. 事实来源。

### 改动结论

先回答“这一块最终变成什么”，再展开细节。

- 新增：只写新增对象、放置位置和最终可见结果。
- 调整 / 删除：合并呈现最终状态。
- 关键复用：写清复用范围与保持不变的结构。

### 关键字段

字段只收录页面上真实存在、开发需要落地的字段或控件：

- 字段名称。
- 控件 / 展示方式。
- 默认值。
- 必填性。
- 可选范围。
- 显示条件。
- 启用条件。
- 校验规则。
- 字段联动。

前置状态、权限条件和处理场景不是字段名称，不得为了“信息完整”塞进字段列表。

### 业务规则

业务规则描述状态、权限、范围、优先级、互斥、可见性或结果约束。

规则应写产品事实，不写接口调用和内部处理步骤。

### 交互说明

每条交互按五段表达：

- 前置状态。
- 用户动作。
- 页面反馈。
- 结果状态。
- 失败反馈。

前置状态放在交互中，不与字段混排。

### 异常反馈

每条异常至少回答：

- 什么情况下触发。
- 页面给用户什么反馈。
- 操作最终被阻断、保留还是回滚。

### 事实来源

右卡展示逻辑来源名称、定位符和 claimPath，不嵌入本机绝对路径、证据正文或
证据包路径。证据文件只在本地校验阶段读取，不打进交付快照。

## 5. 显式界面容器

surface 是用户当前看到并能操作的最上层界面容器，不是业务页面名称的同义词。

允许的 kind：

- `view`
- `drawer`
- `modal`

每个 surface 必须声明：

- `surfaceId`
- `kind`
- `rootSelector`
- `activeSelector`
- `parentSurfaceIds`
- `exitActions`
- `exitGuardSurfaceIds`
- `snapshotSelectors`

`snapshotSelectors` 必须包含该 surface 的 `rootSelector`，使运行时验收覆盖整个可见
业务容器。恢复过程只恢复不会破坏监听器的 class、控件值和滚动状态；禁止用替换
`innerHTML` 制造表面一致。业务 DOM 未通过正常退出 / 进入行为恢复时，运行时签名
必须失败。

`body`、`html`、`:root`、`#root`、`#app` 不能充当业务 surface；anchor 也不能
直接等于 surface root。这样的结构只能说明 DOM 存在，不能证明真实交互位置。

### 最上层活动容器规则

编号、高亮、右卡聚焦和虚线只属于最上层活动容器。

例如 drawer 打开时：

- drawer 卡片可以显示编号和高亮。
- 底层 view 的编号与连线必须隐藏。
- 右卡仍可浏览，但底层卡片不能表现为当前原型标记。

modal 打开时同理，modal 是唯一可标记的 top surface。

如果同时激活两个同级 drawer / modal，或活动容器无法形成唯一父子路径，判定 `E_SURFACE_AMBIGUOUS`，安全隐藏标记，不猜测哪一个更上层。

遇到未注册的可见 overlay，也安全隐藏编号、高亮和连线。

### 容器切换

- 同容器切卡：不关闭、不重建容器，只切换锚点和右卡聚焦。
- 跨容器切卡：先按当前路径退出旧容器，再执行目标卡的进入动作。
- 退出被确认弹窗拦截：暂停切换，等待用户决定。
- 任一步失败：原子恢复到切换前快照。

## 6. 双向定位与返回

右卡到原型：

1. 保存进入定位前的原型快照。
2. 解析目标 surface 路径。
3. 退出不共享的旧 surface。
4. 执行 card 的 `anchor.enterActions`。
5. 断言目标 surface 是最上层活动容器。
6. 断言 anchor 唯一且位于 surface root 内。
7. 断言 anchor 当前可见。
8. 将左侧目标滚入视口，聚焦左侧元素、右侧卡片，并绘制可见虚线。
9. 断言目标、卡片和虚线端点都实际落在当前视口内。

原型到右卡：

1. 仅为 top surface 的卡片挂载编号。
2. 点击编号或对应元素。
3. 聚焦右卡并滚动到可读位置。
4. 保持同一份 cardId 作为双向绑定键。

返回：

- “返回上一个原型状态”恢复首次进入定位前的快照。
- 快照至少保存活动 surface、动态节点、表单控件、滚动位置和必要导航状态。
- 在同一个未刷新的活页面上重复第二轮定位与返回，第二轮也必须恢复到首次基线；
  不允许通过每轮 reload 掩盖闭包变量、计数器或事件监听器泄漏。
- 恢复失败必须展示明确错误，不得保留半切换状态。

## 7. 构建、自动校验和失败语义

推荐先运行：

```bash
node scripts/validate-prototype-handoff.mjs \
  --manifest path/to/change-manifest.json \
  --prototype path/to/prototype.html \
  --evidence path/to/evidence-bundle.json \
  --workspace-root path/to/current-workspace \
  --evidence-root path/to/approved-materials \
  --review path/to/independent-review.json
```

多个材料根目录重复传入 `--evidence-root`。既有系统增量中，`--prototype` 的真实
路径必须等于 `--workspace-root + prototype.sourcePath`，修改前快照必须以
`prototype-baseline` 来源进入 evidence bundle，且发生 add / modify / delete 时
修改前后 Hash 不得相同。

`--review` 只在命中风险触发项时必需。使用
`assets/independent-review-template.json` 作为字段参考，由未参与制作的复核方填写；
一旦三份输入任一内容变化，原记录因 Hash 不匹配而失效，必须对最终版本重新复核。

校验顺序：

1. manifest 与 evidence bundle 的结构、枚举和版本。
2. 所有证据文件存在且 SHA-256 与声明一致。
3. 每个语义 claim 有可解析的 locator / excerpt，并拒绝占位或整卡复制。
4. 既有系统增量的真实页面、定向代码检索、同版本截图链及冲突状态。
5. surface ID、父级引用、环和禁止的虚拟 surface。
6. card 的 surface 归属、selector 唯一性、可见性和真实锚点。
7. enterActions 后的 top surface 与 card.surfaceId 一致。
8. 风险触发项与独立 review record 的对象 Hash、触发项和结论。
9. 右卡→原型、原型→右卡、可见连线、同一活页面两轮返回状态恢复的浏览器运行时验收。
10. full coverage 的页面、卡片和阻断性未决项闭合。

主要错误码：

- `E_CARD_SURFACE`
- `E_SURFACE_PARENT_UNKNOWN`
- `E_SURFACE_PARENT_CYCLE`
- `E_SURFACE_AMBIGUOUS`
- `E_SURFACE_TARGET_INACTIVE`
- `E_ANCHOR_MISSING`
- `E_ANCHOR_AMBIGUOUS`
- `E_ANCHOR_OUTSIDE_SURFACE`
- `E_ANCHOR_NOT_VISIBLE`
- `E_FAKE_ANCHOR_FORBIDDEN`
- `E_SEMANTIC_CLAIM_UNBOUND`
- `E_EVIDENCE_EXCERPT_UNRESOLVED`
- `E_PLACEHOLDER_SEMANTICS`
- `E_RUNTIME_STATE_RESTORE`
- `E_RUNTIME_CONNECTOR`
- `E_RUNTIME_CONNECTOR_NOT_VISIBLE`
- `E_PROTOTYPE_WORKSPACE_IDENTITY`
- `E_PROTOTYPE_INCREMENT_NOT_APPLIED`
- `E_AUTHORITATIVE_SEMANTIC_SOURCE_REQUIRED`
- `E_EVIDENCE_OUTSIDE_TRUSTED_ROOT`
- `E_GLOBAL_RULE_LOCATABLE_SEMANTICS`
- `E_GLOBAL_RULE_SCOPE_EVIDENCE`
- `E_INDEPENDENT_REVIEW_REQUIRED`
- `E_REVIEW_SUBJECT_HASH`
- `E_FULL_COVERAGE_INCOMPLETE`
- `E_RUNTIME_STATE_RESTORE_REPEAT`

自动门至少包含：

- manifest 测试。
- surface 路径与歧义测试。
- anchor 唯一性和归属测试。
- 证据 Hash、locator、excerpt、占位语义和重复语义测试。
- 跨容器退出、同容器切换和失败回滚测试。
- 双向定位、可见连线和返回状态的浏览器运行时测试。
- 只读快照构建测试。
- 业务样例泄漏扫描。

`build-snapshot.mjs` 也强制要求 evidence、workspace root 和 evidence roots，并重复
执行结构、来源、原型身份和阻断项检查；不能绕过 validator 直接构建一个看似
evidence-grounded 的 v4 累计快照。

## 8. 当前整合版与需求快照

构建产物是“当前整合版”：

- 左侧嵌入当次冻结的只读原型。
- 右侧由同一 manifest 生成。
- manifest 与原型来源摘要随构建保存。
- 不读取或回写在线业务系统。

`pilot-slice` 产物必须在顶部写明它不是完整 PRD 页面范围。

只有 `coverage=full` 且：

- 每个 pages 项都有 card 覆盖。
- 每个语义单元都绑定到可解析事实来源，且 card 有真实 surface / anchor 归属。
- 没有 `blocksDelivery=true` 的 pendingItems。
- validator 通过。

才能称为完整需求快照。

## 9. 可选的人类观察

可以邀请未参与制作的开发观察：

- 是否快速找到本次范围。
- 是否能区分字段、前置状态、规则和交互。
- 是否能从右卡定位到原型并返回。
- 是否发现描述歧义或遗漏。

观察结果用于寻找可行动问题并优化字段说明、范围声明、定位或容器模型。

不要把 5/20 分钟达成率、第二名开发、反馈台账或人工通过状态设置成候选或发布的强制门。不得用 Agent 自测冒充真人观察结果。
