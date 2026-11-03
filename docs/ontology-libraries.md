# 本体库（Ontology Libraries）

OMS 从「单一全局本体库」改为**多本体库隔离**。所有概念、算子、规则、个体、标注批次、版本、建议都带 `library_id`，默认查询/写入只作用于当前库。

## 库清单

| key | 名称 | kind | 内容 |
| --- | --- | --- | --- |
| `fibo-cmns` | FIBO + CMNS 参考本体 | reference | 3150 概念 / 3385 算子，只作参考或复制来源 |
| `co-baseline` | CO v0.8 业务基线 | business | 20 概念 / 5 算子；新业务默认库 |
| `pilot-yuer` | 育儿补贴 Pilot | pilot | v1/v2 批次；按库 apply，不影响其他库 |
| `archer` | Archer Benchmark | reference | 639 概念 / 4622 算子；见 `docs/archer-import.md` |
| `keol-yuer` | KEOL 育儿补贴参考本体 | reference | 322 概念 / 224 算子 / 16 规则；KEOL 推送镜像，见 `docs/keol-rule-import.md` |

## 页面

顶栏「本体库」下拉切换，选择写入 cookie `ontology_library`，之后所有本体页面/API 都按该库工作。

- 概念/算子/规则/个体、概念图、全局搜索、导入导出、体检、标注批次/版本、建议箱均按当前库隔离。
- 工作台/GOLD/题库等非本体数据的展示仍沿用原逻辑；涉及概念引用时以当前库为准。
- GOLD（成品/批次/抽检）、题库（questions/ke_tasks/ke_uploads）、Issue（issues 及子表）均已增加 `library_id`，按当前库读写。
- 历史 GOLD 按生成来源回填：DataMaker `DM-*` 回填到对应 job 的库；NL2KE 回填到对应 run 的库；其余回默认业务库。
- GOLD 草稿审查：`/gold/review`，支持来源筛选、校验筛选、批量复核/退回/删除。
- Pilot v1/v2 只出现在 `pilot-yuer`；v1 apply 只写这个库。

## API

推荐显式传入：

```http
X-Ontology-Library: co-baseline
```

也支持 `?library=co-baseline` 或 cookie。读操作不传默认 `co-baseline`；**写操作不应依赖默认值**。无效的 API 库 key 返回 400，避免误写到别的库。

```bash
curl -H "Authorization: Bearer ke_..." \
     -H "X-Ontology-Library: fibo-cmns" \
     https://192.168.120.3:8443/api/concepts
```

库注册表：

- `GET /api/libraries`（登录用户）
- `POST /api/libraries`（admin，创建空库）

## 唯一性与引用

- 概念：`(library_id, name)` 唯一，因此同名概念可在不同库共存。
- 概念另有库内编号 `local_code`：`ARCH-0001` / `FIBO-0001` / `CO-0001` / `PILOT-0001`；列表、详情、API 和搜索均可查，全局 `id` 仍用于外键和链接。
- 概念 `description` 和算子 `description` 已全量填写；Archer 概念按来源库/表/字段生成，Archer 算子保留原始 note。
- 规则：`(library_id, name)` 唯一。
- 版本：`(library_id, label)` 唯一。
- 算子：沿用 `(name, subject_concept_id)`；主体概念 ID 本身已按库隔离。
- 个体：沿用 `(concept_id, name)`。
- 批次/单元/证据/CO/对齐/KE/GOLD/版本/建议：全部带库，按库读取。
- **不做隐式跨库继承**；需要参考本体时，显式导出/导入到目标库。

## 迁移与回填

迁移 `n5b6c7d8e9f0`：

1. 建 `ontology_libraries`，写入上面三个库；
2. 所有相关表加 `library_id`；
3. 按 domain/module 回填 FIBO/CMNS → reference，CO v0.8 → business；
4. v1/v2 及其标注子表 → pilot；
5. 全局唯一索引改为库内唯一。

新库由 `alembic upgrade head` 自动获得三库；旧库升级前先备份。

## 成员权限

`library_members` 保存 `(library_id, user_id, role)`：

- `owner`：库管理员，可添加/移除库成员；全局账号仍需具备编辑能力才能改本体。
- `reviewer`：库内审核/编辑。
- `annotator`：库内只读、提交和标注。

有效角色 = 库角色与账号角色取较低者（admin 例外，全局通过）。没有成员的账号无法读取该库，直接访问 URL/API 会 403。页面 `/libraries` 可查看/管理授权；新账号默认无库权限，由库管理员授权。

## 安全边界

- `apply`、版本冻结/发布、建议审批都只作用于当前库。
- KEOL 拉取/推送带库 key：默认 `co-baseline`，参考库可选 `fibo-cmns`，政策导入固定 `pilot-yuer`。
- 备份仍是整个数据库（所有库）；回滚整个库请用数据库备份，不要用单条概念回滚代替。
- 标注看板：`/annotation/dashboard`；Issue 导出：`/issues/{id}/export.md`、`/annotation/batches/{id}/issues.md`。

## 库上下文约定

- Web 请求由 `OntologyLibraryMiddleware` 设置 `library_scope`。
- 后台任务禁止依赖线程默认库，必须使用 `library_session(library_id)` 或显式 `library_scope`。
- 核心概念/算子 service 支持显式 `library_id=`，核心 API 路由已传入当前库 ID。
- `DataMaker` 使用独立 worker；定时体检逐库执行。
- 维护脚本跨库操作必须显式 `library_scope(None)`。
