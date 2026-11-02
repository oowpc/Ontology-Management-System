# OMS 当前工作流

> 更新时间：2026-10-28  
> 生产地址：`https://192.168.120.3:8443/`（OMS，内部 CA 签发）；`https://192.168.120.3:8444/oms`（KEOL 加工台，内部 CA 签发）  
> 回环端口：OMS `127.0.0.1:8020`；KEOL `127.0.0.1:8321`；对外只开 8443/8444。  
> 业务规则以 `docs/co-ke-annotation-sop.md`（SOP v0.5）为准；本文描述系统**当前已实现**的行为。两者不一致处见第 10 节。

## 0. 算子输入/输出

算子签名统一按 KEOL 风格展示：

```text
op 机构_是否有人(机构, 人)：布尔
```

- `operator_inputs` 按顺序保存输入；输入能匹配本库概念时写 `concept_id`，否则只存类型名。
- 主体概念自动作为第一个输入；表单里的「输入参数」填写其余输入。
- `return_type` 保存 `Bool`/`String`/概念名等；有同名概念时自动写 `output_concept_id`。
- 页面详情、列表、`/api/operators`、版本 payload、JSON 导入导出和 KEOL 桥都使用该结构。

## 0.1 权限与看板

- 库成员表 `library_members`：owner/reviewer/annotator；无成员账号不能访问该库。
- 有效角色取库角色和全局账号角色的较低者；admin 全局通过。
- 库管理页 `/libraries`。
- 标注运营看板 `/annotation/dashboard`：跨批次进度、证据覆盖、Issue、LLM 审核、阻塞单元。
- Issue 文档：单条 `/issues/{id}/export.md`，批次汇总 `/annotation/batches/{id}/issues.md`。

## 1. 系统定位

OMS 是本体与标注治理的主库，KEOL 加工台是文档抽取与规则加工的工作台。

- **OMS 负责**
  - 多本体库：`fibo-cmns` 参考库、`co-baseline` 业务基线、`pilot-yuer` 试点库（详见 `docs/ontology-libraries.md`）。
  - 库内本体：概念（支持多父类）、算子、规则、个体。
  - 本体来源、状态、领域、模块、审计。
  - CO/KE 手工标注批次、双盲、对齐、合并、GOLD、交叉检查、版本。
  - SQL/LEAN/SPARQL 单元的执行/编译留痕。
  - JSON/CSV 导入导出、版本归档。
- **KEOL 加工台负责**
  - 文档切分、抽取、规则/个体编辑。
  - 通过 `/oms` 页面从 OMS 拉取，或向 OMS 推送当前工作本体。
- **当前边界**
  - OMS 没有全局常量表；常量只存在于 CO/对齐/KE/GOLD 的 JSON 中。
  - 规则/个体是库内层，不参与 CO/KE 双盲；它们通过 OMS 页面、JSON 导入或 KEOL 推送进入。
  - KEOL 推送默认进入 **OMS 本体建议箱**，审批后才写当前库；`mode=direct` 仅为管理员应急直写。
  - 页面/API 用顶栏或 `X-Ontology-Library` 选择库；apply/版本/建议都只作用于当前库。

## 2. 角色

### 2.1 系统角色

| 角色 | 能力 |
|---|---|
| `annotator` | 浏览概念/算子/规则/个体；在所属批次中按 A/B 侧提交 CO/KE；参与对齐、GOLD |
| `reviewer` | 概念/算子/规则/个体增改；创建标注批次；审核 |
| `admin` | 全部权限；可看所有批次、执行所有状态迁移、管理用户/Token/导入导出 |

### 2.2 批次成员角色

批次成员与系统角色独立；同一个用户可以同时是系统 `reviewer` 和某批次 `annotator`。

| 批次角色 | 作用 |
|---|---|
| `annotator` + `side=A/B` | 独立完成本侧 CO/KE |
| `lead` | 组长：排期、冻结、状态推进、合并日志 |
| `batch_owner` | 批次负责人：批次维护与状态推进 |
| `arbiter` | 裁决人：对齐/合并中的争议 |
| `cross_checker` | 交叉检查；至少 2 人才能进入交叉检查阶段 |
| `project_owner` | 项目负责人：批准发布、最终 `pass` |

关键权限：

- 创建批次：`reviewer` / `admin`。
- CO/KE：只能提交自己 A/B 侧的记录；`admin` 不受限。
- 对齐/GOLD：`annotator`、`lead`、`arbiter`、`admin`。
- 交叉检查：`cross_checker`、`lead`、`admin`。
- 版本冻结：`lead` / `batch_owner` / `admin`。
- 版本发布、批次最终通过：`project_owner` / `admin`。
- 批量归档：`lead` / `batch_owner` / `project_owner` / `admin`。

## 3. 数据对象

### 3.1 全局本体

| 对象 | 说明 |
|---|---|
| `Concept` | 概念；名称自然键；`domain`、`aliases`、`definition`、`source_uri`、`ontology_module`、`status` |
| `ConceptParent` | 多父类链接；主父类 + 声明顺序 |
| `Operator` | 算子；`(name, subject)` 为身份；主体/返回类型/参数类型 |
| `OntologyRule` | 全局规则；名称唯一；`body`/`head` 为 spec_v1 JSON；`body=NULL` 表示事实 |
| `OntologyIndividual` | 全局个体；`(concept_id, name)` 唯一 |
| `ImportBatch` | 导入批次与统计 |
| `AuditLog` | 所有写操作的前后快照 |

规则/个体的结构引用：

- OMS 内部写 `oms-c-<概念ID>`、`oms-op-<算子ID>`，避免同名算子歧义。
- 规则 `body` 形状：`Formula` / `Assertion` / `null`。
- 规则 `head` 形状：非空 `Assertion` 数组。
- 个体必须挂在一个已存在的概念下。

### 3.2 标注批次

| 对象 | 说明 |
|---|---|
| `AnnotationBatch` | 批次、来源、校验和、指南版本、统计单位、验收门槛、状态 |
| `AnnotationMember` | 批次成员与 A/B 侧、组名 |
| `AnnotationUnit` | 标注单元；`unit_id` 在批次内唯一；原文/译文/来源快照 |
| `AnnotationEvidence` | evidence：来源 ID、定位、原文片段、语言 |
| `CoAnnotation` | 单侧 CO；A/B 双盲；版本化、历史只读 |
| `CoAlignment` | 逐题对齐结论；引用 CO_A/CO_B；允许 `pending` |
| `CoMergeLog` | 合并/拆分/改名/改父类日志 |
| `OntologyVersion` | 本体版本；快照、计数、校验、冻结/发布 |
| `KeAnnotation` | 单侧 KE；A/B 双盲；可带执行/编译结果 |
| `GoldKe` | GOLD KE；来源 A/B/合成；逐题决议 |
| `AnnotationCrossReview` | 交叉检查结论；`pass` / `changes` / `block` |
| `QueryRun` | SQL/SPARQL/LEAN 执行/编译记录 |
| `Issue` | 分歧、待裁决、返工等问题的跨对象记录 |

## 4. 总览

```text
数据集 / Query / 文本
  -> 创建标注批次 (draft)
  -> 导入标注单元 + 成员 + evidence
  -> freeze（数据集冻结）
  -> CO 双盲（A/B）
  -> 逐题 CO 对齐
  -> CO_M 应用（写全局概念/算子 + 创建版本草稿）
  -> 版本 freeze / release
  -> KE 双盲（A/B；SQL/SPARQL/LEAN 需执行留痕）
  -> GOLD 合并
  -> 交叉检查（≥2 人）
  -> validation
  -> pass（需已发布版本）
  -> 归档 ZIP
```

全局本体另有独立入口：

```text
FIBO / CMNS / JSON / OMS 页面 / KEOL 推送
  -> 概念 / 算子 / 规则 / 个体
  -> 版本快照 + 校验
  -> freeze / release
  -> KEOL 拉取（当前为实时库；版本钉住尚未实现）
```

## 5. 批次状态机

| 动作（POST `/annotation/batches/{id}/state`） | 当前状态 | 下一状态 | 系统门槛 |
|---|---|---|---|
| `freeze` | `draft` | `dataset_frozen` | 有单元；来源名、指南版本、校验和齐全；A/B 标注者齐全；单元有原文或来源快照；英文单元有译文；Query 单元有 `source_payload` |
| `start_co` | `dataset_frozen` | `co_blind` | 无额外门槛 |
| `start_alignment` | `co_blind` | `co_alignment` | 所有单元都有 A/B 当前 CO |
| `start_merge` | `co_alignment` | `co_merge` | 所有单元都有对齐记录（**允许 `pending`**） |
| `start_ke` | `co_merge` | `ke_blind` | 本批次存在 `frozen` 或 `released` 版本 |
| `start_gold` | `ke_blind` | `gold_merge` | 所有单元都有 A/B KE；SQL/LEAN/SPARQL 每侧有执行结果或已通过的 `QueryRun` |
| `start_cross` | `gold_merge` | `cross_review` | 所有单元都有 GOLD；交叉检查成员 ≥2 |
| `validate` | `cross_review` | `validation` | 最近一条交叉检查结论为 `pass` |
| `pass` | `validation` | `passed` | 本批次存在 `released` 版本 |
| `rework` | 非 `passed` | `rework` | 组长/批次负责人/管理员；已通过批次不能直接返工 |
| `resume` | `rework` | 指定状态 | 目标状态不能是 `draft` 或 `passed` |

状态标签：

`draft` 草稿 → `dataset_frozen` 数据集已冻结 → `co_blind` CO 双盲中 → `co_alignment` CO 逐题对齐中 → `co_merge` 本体合并中 → `ke_blind` KE 双盲中 → `gold_merge` GOLD 合并中 → `cross_review` 交叉检查中 → `validation` 待校验 → `passed` 已通过；任意环节可进入 `rework` 返工中。

## 6. 标准操作流程

### 6.1 创建批次与准备

入口：`/annotation` → 新建批次。

批次字段：

`batch_id`、`name`、`source_name`、`source_version`、`source_checksum`、`source_type`、`domain`、`guide_version`、`statistical_unit`、`acceptance_threshold`、`lead`、`annotator_a`、`annotator_b`、`arbiter`、`cross_checkers`（逗号分隔）、`project_owner`、`note`。

成员补充：批次详情页可继续添加成员；角色必须是 `annotator`（带 A/B 侧）、`lead`、`arbiter`、`batch_owner`、`cross_checker`、`project_owner`。

标注单元导入：`POST /annotation/batches/{id}/units/import`，表单字段 `units_json` 为数组。单个单元常用字段：

```json
{
  "unit_id": "u-001",
  "source_id": "doc-001",
  "source_type": "policy",
  "source_locator": {"page": 1, "paragraph": 2},
  "raw_text": "……",
  "translation_text": "",
  "language": "zh",
  "source_payload": null
}
```

冻结前的系统检查见第 5 节。**系统不强制每条都有 evidence**；SOP 要求 evidence 齐备，证据质量由流程和交叉检查保证。

### 6.2 CO 双盲

入口：批次详情 → 单元详情；或 `POST /annotation/units/{uid}/co`。

提交字段：`side`（A/B）、`decision`（include 等）、`concepts`、`constants`、`operators`、`reason`、`evidence_ids`。

隔离规则：

- 批次处于 `co_blind` 时，非管理员/组长/裁决人只能看到自己 A/B 侧的 CO。
- 交叉检查者在 `cross_review` / `validation` / `passed` / `rework` 之前看不到单元数据。
- 隔离是服务端视图过滤，不是物理数据库隔离；SOP 要求的“不同副本/目录”仍应由操作流程保证。

每次提交都会新增一条 `CoAnnotation`，旧版本置为非当前；历史保留。

### 6.3 CO 逐题对齐

入口：单元详情；或 `POST /annotation/units/{uid}/align`。

提交字段：

- `concepts` / `constants` / `operators`：对齐后的行文本；
- `concept_rows`：概念 JSON 数组（推荐）；
- `operator_rows`：算子 JSON 数组（推荐）；
- `differences`：分歧点；
- `conclusion`：`aligned` / `pending` 等；
- `reason`、`evidence_ids`。

`conclusion=pending` 的记录会进入 `aligned_pending`；`submit_ke`、`start_merge` 和 CO_M 应用都会拒绝 `pending`。

### 6.4 CO_M 应用

入口：批次详情 → “应用对齐结果”；或 `POST /annotation/batches/{id}/apply`。

- 只允许在 `co_merge` 或 `rework` 状态执行，组长/批次负责人/管理员操作。
- 遍历所有单元当前对齐记录：
  - 先建设概念（支持 `concept_rows`，否则用 `concepts` 行）；
  - 再挂父类，检测父类存在与环；
  - 再建算子（支持 `operator_rows`，否则解析 `operators` 行）；
  - 常量只计数，不写入全局表；
  - 每次合并写 `CoMergeLog`。
- 自动创建 **一个版本草稿** `CO_M-<batch>-<随机后缀>`；父版本取最近一个 `released` 版本。
- 返回/页面显示新增、更新、跳过统计。
- 不会从 CO 对齐结果自动创建规则或个体；规则/个体走全局规则页、导入或 KEOL 推送。

### 6.5 版本校验、冻结、发布

入口：`/annotation/versions` 或批次详情。

- **创建草稿**：`POST /annotation/batches/{id}/versions`，字段 `label`、`note`、`parent_version_id`、`import_batch_id`。
- **冻结**：`POST /annotation/versions/{vid}/freeze`；要求版本校验 `ok`。
- **发布**：`POST /annotation/versions/{vid}/release`；要求 `frozen`，项目负责人/管理员。

版本快照与校验包括：

- 概念名称哈希和数量；
- 算子名称 + 返回类型哈希和数量；
- 规则名称 + body/head 哈希和数量；
- 个体（概念名 + 个体名）哈希和数量；
- 校验：父类存在/无环、算子主体与返回类型、个体所属概念存在、规则节点形状、规则中的 OMS ID 引用存在。
- 内建/外部引用（不是 `oms-c-*` / `oms-op-*` 的引用）不会被判为悬空。

`start_ke` 只要求存在冻结或发布版本；`pass` 要求存在发布版本。

### 6.6 KE 双盲

入口：单元详情；或 `POST /annotation/units/{uid}/ke`。

提交字段：`side`、`content`、`evidence_ids`、`execution`、`self_check`。

- 单元必须先有对齐记录且 `conclusion != pending`。
- 只能提交自己 A/B 侧；`ke_blind` 期间互不可见（管理员/组长/裁决人除外）。
- 每次提交新增版本，旧版本置为非当前；KE 必须关联 evidence。

SQL / SPARQL / LEAN：

- 执行入口：`POST /annotation/units/{uid}/run-query`，字段 `side`。
- 可在 `ke_blind`、`gold_merge`、`cross_review`、`validation`、`rework` 执行。
- SQL：`source_payload.schema_sql` + `target_sql`，内存 SQLite 做 `EXPLAIN`，只允许单条语句。
- SPARQL：`source_payload.rdf`（或 `ttl`）+ `target_sparql`，rdflib 执行。
- LEAN：`source_payload.lean_source` + 环境变量 `LEAN_BIN` 或 PATH 中的 `lean`；当前生产未安装 LEAN，实际执行会提示未配置。
- 每次执行写 `QueryRun`（命令、状态、stdout/stderr、结果）。
- `start_gold` 对 Query 单元要求每侧有 KE 的 `execution` 或已通过的 `QueryRun`。

### 6.7 GOLD 合并

入口：单元详情；或 `POST /annotation/units/{uid}/gold`。

字段：`source`（A/B/合成/重标等）、`content`、`differences`、`reason`、`evidence_ids`。

- 只有 A/B KE 都齐备才能提交。
- 每次提交新增 `GoldKe` 版本，旧版本置为非当前；GOLD 也必须关联 evidence。
- `start_cross` 要求所有单元都有 GOLD。

### 6.8 交叉检查

入口：批次详情；或 `POST /annotation/batches/{id}/cross-review`。

字段：`scope`、`conclusion`（`pass` / `changes` / `block`）、`findings`。

- 交叉检查成员至少 2 人；`start_cross` 时检查。
- 交叉检查成员不能同时是 A/B 标注者，且必须包含来自不同组的成员。
- 系统允许 `lead`/`admin` 提交交叉检查结论；SOP 的完整独立性要求仍需流程保证。
- `validate` 要求**最近一条**结论为 `pass`；`changes`/`block` 后处理完再提交新的 `pass`。

### 6.9 校验、通过与归档

- `validate`：交叉检查通过后进入 `validation`。
- `pass`：项目负责人/管理员确认已发布版本后进入 `passed`，写 `finished_at`。
- 归档：`GET /annotation/batches/{id}/export`，仅组长/批次负责人/项目负责人/管理员。
- ZIP 包含：批次、单元、evidence、CO、对齐、KE、GOLD、query_runs、版本、merge_logs、交叉检查、issues、audit、README。

### 6.10 返工

- `rework`：任意非 `passed` 状态可进入；`passed` 不能直接返工。
- `resume`：从 `rework` 回到指定状态，不能回 `draft` 或 `passed`。
- 返工原因写入批次 `note`，状态迁移写审计。

## 7. 全局规则 / 个体工作流

### 7.1 OMS 页面

- 规则：`/rules`；新建、编辑、查看；`body`/`head` 为 JSON 文本框；状态 `draft/pending/approved/disabled`。
- 个体：`/individuals`；所属概念用概念选择器；个体身份为 `(概念, 名称)`。
- 规则/个体现为全局层，不与某个标注批次绑定；批次版本只做快照和校验。
- Agent 可直接用 `GET/POST /api/rules`、`GET/PATCH /api/rules/{id}` 与对应 `/api/individuals`；MCP 也有 `list_rules` / `create_rule` / `list_individuals` / `create_individual` 等工具。

### 7.2 导入导出

- `GET /api/export/json`：包含 `concepts`、`operators`、`rules`、`individuals`。
- `POST /api/import/json`：同结构；规则按名称 upsert，个体按 `(concept_id, name)` upsert。
- 规则的 `body`/`head` 结构引用建议写 OMS ID：
  - 概念：`oms-c-<id>`
  - 算子：`oms-op-<id>`
- 规则/个体的 `created_at`/`updated_at` 导出为 ISO 字符串，导入时由系统重新生成。

### 7.3 KEOL 拉取

入口：`https://192.168.120.3:8444/oms` → “从 OMS 拉取”；或 `POST /api/oms/pull`。

- 模式：
  - `merge`：保留当前本体；OMS 概念/算子按名称复用本地 ID；规则/个体引用映射到本地 ID。
  - `replace`：先备份 `console/data/ontology.json`，再用 OMS 全量替换。
- 拉取内容：概念、算子、规则、个体。
- ID 映射：
  - 概念 `oms-c-<OMSID>`
  - 算子 `oms-op-<OMSID>`
  - 规则 `oms-rule-<OMSID>`
  - 个体 `oms-ind-<OMSID>`
- `limit>0` 时只拉概念/算子，规则/个体跳过（用于抽样试验）。
- 状态页显示 OMS 的概念/算子/规则/个体计数。
- 可填 `version_id` 拉取冻结/已发布版本（`/api/annotation/versions/{vid}/payload`），返回 `version_id` 用于记录；留空仍拉实时库。

### 7.4 KEOL 推送

入口：`/oms` → “推送到 OMS”；或 `POST /api/oms/push`。默认 `mode=suggest`，提交到 OMS **本体建议箱**，不写全局；审核通过后才应用。`mode=direct` + `confirm_direct=true` 是管理员应急直写。

建议包：

1. 概念/算子只包含 OMS 缺失项；规则/个体只包含缺失项；
2. 包内保留 KEOL 引用 ID 与 `refs` 映射；
3. OMS 审批时先导入概念/算子，再把规则引用映射成 OMS ID，最后导入规则/个体。

规则/个体推送限制：

- 引用无法映射到 OMS 算子/概念的规则会被跳过，统计里返回 `skipped_rules`。
- 内置算子/概念引用会保留为 KEOL 内置 ID；OMS 校验将其视作外部引用。
- 不更新已存在条目，不删除 OMS 条目。
- 建议包审批前不进入全局；审批后写入并可审计。
- 当前 OMS 规则/个体结构引用是 **OMS 实例 ID**；跨 OMS 实例迁移规则 JSON 需要重新映射。

### 7.5 安全边界

- OMS Token 仅放在服务端 `~/.keol-console.env`，前端不接触。
- KEOL 加工台 8321 已加登录：复用 OMS 账号（`/api/auth/login`），Session 由控制台维护；拉取/推送要求 OMS reviewer/admin，服务可用 `KEOL_CONSOLE_TOKEN` 走 Bearer。
- OMS 的写权限由 Token scope 决定；桥接使用 admin write Token。

## 8. 当前生产状态

截至 2026-10-28：

| 项目 | 状态 |
|---|---|
| OMS | `https://192.168.120.3:8443/`（内部 CA；回环 8020） |
| KEOL 加工台 | `https://192.168.120.3:8444/oms`（内部 CA；回环 8321） |
| OMS 数据 | 3170 概念（FIBO 2992 + CMNS 158 + CO v0.8 业务基线 20）/ 3390 算子 / 0 规则 / 0 个体 |
| Pilot 批次 | `PILOT-20261028-01`（《育儿补贴制度实施方案》完整原文，18 单元，`co_alignment`）；v2 原子切分返工审计 |
| KEOL 当前本体 | 322 概念 / 229 算子 / 16 规则 / 0 个体（独立工作本体） |
| OMS 迁移 | `k2e3f4a5b6c7`（全局规则/个体） |
| 备份 | `data/backups/`，迁移前后均有 SQLite 备份 |
| 规则/个体入口 | OMS 顶部导航“规则”“个体”；KEOL `/oms` 推送 |

当前 OMS 规则/个体表为空；测试概念与测试提交链已清理，批量基线说明见 `docs/ontology-baseline.md`。`PILOT-20261028-01` 已恢复为 active（完整原文），A/B CO 与对齐已保留；v2 原子切分转返工。AI 预跑报告见 `docs/pilots/PILOT-20261028-01-report.md`。KEOL 的 16 条规则还没有写入生产 OMS；需要时在 `/oms` 勾选确认推送。

## 9. 快速检查清单

### 9.1 标注批次

- [ ] 批次来源、版本、校验和、指南版本已填。
- [ ] 单元 `unit_id` 唯一且覆盖全部输入。
- [ ] A/B 标注者已指定；交叉检查 ≥2 人。
- [ ] `freeze` 通过。
- [ ] CO A/B 全部提交，逐题对齐完成。
- [ ] CO_M 应用后版本草稿已校验、冻结、发布。
- [ ] KE A/B 全部提交；Query 单元有执行留痕。
- [ ] GOLD 全部完成。
- [ ] 交叉检查最近结论为 `pass`。
- [ ] 版本已发布，批次 `pass`。
- [ ] 归档 ZIP 已导出。

### 9.2 全局规则/个体

- [ ] 规则 `body/head` JSON 合法，`head` 非空。
- [ ] 规则结构引用使用 `oms-c-*` / `oms-op-*`，且 ID 存在。
- [ ] 个体所属概念存在，`(概念, 名称)` 不重复。
- [ ] 需要版本时，已创建版本并通过校验。

### 9.3 KEOL 同步

- [ ] OMS Token 已配置且服务端可用。
- [ ] 拉取前确认 merge/replace；replace 会备份。
- [ ] 推送前确认 create-only，不会覆盖已有条目。
- [ ] 查看 `stats` 中的 `skipped_rules` / `skipped_individuals`，确认没有意外跳过。
- [ ] 推送后到 OMS `/rules`、`/individuals` 抽查。

## 10. 当前实现边界（重要）

以下是当前系统与 SOP 的已知差异，后续 P1/P2 应优先处理：

1. **版本快照与钉取已补齐**：版本保存完整 payload，可 `version_id` 拉取；`rollback` 当前生成回滚草稿，不自动覆盖全局。
2. **标注硬门槛已补齐**：CO/KE/GOLD 至少一条 evidence；pending 对齐不能合并；交叉检查禁止与 A/B 重叠且必须有跨组成员。
3. **CO_M 只建概念/算子**：常量只计数；规则/个体不会从 CO_M 自动生成。
4. **KEOL 推送建议闭环已补齐**：默认进入建议箱，审批后应用；admin `mode=direct` 仍是应急直写。
5. **规则/个体跨实例引用**：规则 JSON 使用 OMS 实例 ID，跨 OMS 导入需要重新映射。
6. **LEAN 执行器**：生产未配置 `LEAN_BIN`，LEAN 单元当前只能记录执行需求，不能实际编译。
7. **KEOL 8321 登录已加**：复用 OMS 账号；拉取/推送按 reviewer/admin 或服务 Token 限制。
8. **HTTPS**：当前仍为内网 HTTP；Nginx/Caddy TLS 需运维权限，配置见 `docs/ops-hardening.md`。
9. **Token**：默认 30 天过期、限流、记录 last_used_ip，写审计带 token_name；不是 OAuth/SSO。

更完整的 Agent 开放度矩阵（REST/MCP/Token/覆盖缺口）见 `AGENTS.md` 第 3 节；当前 manifest 实测 160 个 API 端点，MCP 83 个工具。

## 11. 验证基线

当前仓库自测：

- OMS：`pytest`（覆盖率门禁 80%，当前约 82%）。
- KEOL 控制台：`pytest` 75 passed。
- Playwright：smoke 43/43；audit 71/71；基线在 `~/oms-ui-screenshots/baseline`。
- 端到端：临时 OMS 实例验证了 push（概念/算子 → 规则/个体）与 pull replace/merge 的 ID 映射。
- Agent API：`tests/test_annotation_api.py` 跑通 JSON 全流程（批次/单元/CO/对齐/CO_M/版本/KE/GOLD/交叉检查/归档）；`tests/test_library_api.py` 跑通规则/个体 CRUD 与 manifest 暴露。
- 生产只读核验：`/api/agent/manifest` 160 个 API 端点；`/api/rules`、`/api/individuals`、`/api/annotation/batches` 均返回 200；MCP 83 个工具。

### 2026-10-28 工作流实测

使用生产库的**临时副本**（不写生产）启动 OMS，按本文流程跑通一个 2 单元批次：

- 批次创建 → 单元导入 → evidence → `freeze` → CO 双盲 → 逐题对齐 → CO_M 应用。
- 全局规则/个体通过 `/api/import/json` 导入。
- 创建并发布包含规则/个体的版本（实测计数：3173 概念 / 3391 算子 / 1 规则 / 1 个体，校验 `ok`）。
- `start_ke` → KE A/B → GOLD → 交叉检查 2 人通过 → `validate` → `pass`。
- 归档 ZIP 14 个文件；全局导出 `/api/export/json` 能看到测试规则/个体；`/rules`、`/individuals` 页面 200。
- 临时服务日志无 ERROR/Traceback。

说明：该实测在**开启 CSRF** 的临时 OMS 上通过（登录页提取 session token，后续表单/接口携带 token），并使用临时库；生产库未写入测试批次。

## 12. 关键代码位置

| 范围 | 文件 |
|---|---|
| 标注状态机/服务 | `app/annotation/service.py` |
| 标注页面路由 | `app/annotation/router.py` |
| 标注页面模板 | `app/templates/annotation/` |
| 全局规则/个体页面 | `app/routers/library.py`、`app/templates/rules/`、`app/templates/individuals/` |
| 全局模型 | `app/models.py`（`OntologyRule`、`OntologyIndividual`） |
| 导入导出 | `app/importer.py`、`app/routers/io.py` |
| 版本校验 | `app/annotation/service.py`（`validate_ontology`、`create_version`） |
| KEOL 桥 | `KEOL/console/server/oms.py`、`KEOL/console/server/routes/oms.py` |
| KEOL 页面 | `KEOL/console/static/oms.html` |
| 业务 SOP | `docs/co-ke-annotation-sop.md` |
| 迁移 | `migrations/versions/`（当前 head `k2e3f4a5b6c7`） |
