# 合成成员管理需求

这是一组虚构产品的原材料，不包含客户数据。请使用当前 prototype-design-workflow 完成下列两份交付设计，并实际编写可执行的交互模型。此次仅验证卡片语义和状态行为，不构建正式 Standard、不做浏览器或业务审查。

## 现状材料与已确认来源

`baseline.mjs` 是当前系统定向采集的完整交互模型，不能修改。现有页面采用列表→详情抽屉→成员组弹窗的层级；成员组弹窗左侧搜索候选、右侧查看已选并调整顺序，确认只写回详情草稿，取消不改变草稿，详情保存才写回账号。详情也有地区选择器、保存、重置。列表编辑筛选条件后点击查询才生效，重置恢复全部。

本例来源已确认可用于上述行为与结构，沿用这份裁决；没有真实页面／截图，不得补造浏览器验收或阻塞这次有限的模型试做。所有初始成员类型为空；这是新增字段，不代表新增成员。

## 当前批准需求

1. 原成员列表增加“成员类型”筛选，可选个人、团队、合作方，沿用原查询方式。
2. 原成员详情增加成员类型选择，三个选项单选；编辑时回填，保存时写回。原有姓名、成员组、地区、保存和重置交互不变。
3. 新增“成员类型管理”页面，展示类型名称与使用账号数，提供查询／重置、编辑；此次固定三种类型，不支持增删或排序。列表筛选和详情使用这三种类型。
4. 成员组及地区选择器没有本期变化；不能把成员组改成普通平铺勾选。

## 交付 A：首次制作累计阅读稿

当前没有旧原型或旧阅读稿。按上述基线与需求制作卡片组织草案；记录哪些原功能保留、哪些能力新增／调整，并编写生成结果的交互模型。

在输出目录生成 `interaction.mjs`，保留 baseline.mjs 的公开导出接口和各方法，以新增导出 `MEMBER_TYPES`（字符串数组）表示三个类型；`createModel` 扩展 `setTypeFilter(type)`、`setDetailType(type)`、`typeUsage()`。`typeUsage()` 返回类型到当前使用账号数的对象。不得通过引用 baseline.mjs 保留行为，应提交自包含模型。原 UI 容器及搜索／排序／确认／取消的继承关系写入下面的草案。

生成 `initial-cards.json`：
```
{
  "lineage": {"mode": "initial", "addedCardIds": []},
  "preserved": [{"operation": "...", "expectedResult": "..."}],
  "cards": [{"cardId": "...", "region": "...", "title": "...",
    "changeKinds": ["add 或 modify 等"], "changes": ["..."],
    "preservedOperations": ["..."], "rules": ["..."], "interactions": ["..."]}],
  "componentInheritance": {"groupPicker": {"container": "...", "operations": ["..."]}}
}
```
这是草案，不是可供正式 Gate 使用的 Manifest。不要声称它通过了 Runtime Audit。

## 交付 B：修改已有累计稿

同一需求的另一份已有草稿 `previous-cards.json` 由用户要求精简，内容与现行需求不一致时修正。生成 `revised-cards.json`，使用上述 cards 格式和 `lineage: {mode: "revision", retainedCardIds: [], updatedCardIds: [], removedCardIds: [], addedCardIds: []}`，保留合理的旧卡身份，并记录 `removedRuleDestinations`（被删旧卡 ID → 承接规则的新卡 ID 数组）。不重写实际业务原型。
