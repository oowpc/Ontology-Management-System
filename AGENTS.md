# Agent 操作手册（本体管理系统 / GOLDKE）

本系统对 agent **开放**：概念/算子/提交/GOLD/KE/Issue/题目等核心数据面，以及标注批次/版本、全局规则/个体，均有 JSON API；标注与规则/个体另提供 MCP 工具。KEOL 桥是独立控制台（8444 TLS，OMS 账号登录），不属于 OMS 的 agent API。

> 数据基线：多本体库隔离。`fibo-cmns` 3150 概念/3385 算子；`co-baseline` 20/5；`pilot-yuer` 只放 Pilot；`archer` 639/4622。详见 `docs/ontology-libraries.md` 和 `docs/archer-import.md`。
>
> **写操作必须带 `X-Ontology-Library: <key>`**（或 `?library=<key>`）；读不传默认 `co-baseline`。
>
> 首次接入先做两件事：① 按 §1 拿 Token；② `GET /api/agent/manifest` 拿活清单（全部 `/api/` 路由 + 当前工况词表 + 工作流配方，运行时生成）。manifest 不包含 `/annotation`、`/rules`、`/individuals` 等页面路由。

## 0. 活清单（推荐入口）

```bash
curl -H "Authorization: Bearer ke_..." http://127.0.0.1:8000/api/agent/manifest
```

返回：按域分组的全部 `/api/` 路由、当前工况词表（GOLD 阶段/泳道时段/组别/目标、KE 状态词表与列名映射、查重阈值、LLM 是否可用）、常用工作流、幂等键与错误约定。另有 MCP server（`scripts/mcp_server.py`，83 个工具，见 §6）；MCP 工具覆盖概念/算子/GOLD/KE/Issue/KEOL 镜像、标注全流程、规则/个体 CRUD、建议箱审批；不含 KEOL 推送/拉取。

## 1. 认证

```bash
# 方式一：用账号密码登录拿 session，再自助签发 Token（推荐）
curl -c ck.txt -d "username=annotator&password=annotator" http://127.0.0.1:8000/login
curl -b ck.txt -X POST http://127.0.0.1:8000/api/tokens -H "Content-Type: application/json" -d '{"name":"my-agent"}'
# → {"token": "ke_..."}   ⚠ 明文仅此一次返回
# 最小权限（推荐）：签发只读 token 用 -d '{"name":"ro","scopes":["read"]}'（缺省读写全权限）

# 方式二：请管理员代签（admin 可为任意用户签发）
```

之后所有请求：`Authorization: Bearer ke_...`；吊销：`DELETE /api/tokens/{id}`。
（P0：登录连续失败 5 次锁 10 分钟；Cookie 会话的表单 POST 需带 CSRF token，**Bearer 调用免校**——agent 不受影响。）
自检：`GET /api/me`。在线文档：`/docs`（OpenAPI）或 `/openapi.json`。

## 2. 端点速查

| 域 | 端点 | 说明 |
| --- | --- | --- |
| KE 进度 | `POST /api/ke/upload` | multipart CSV **全量上传**（幂等：按 组+题目ID 覆盖；带「篇编号+KE内容」列会同步入成品库草稿） |
| | `GET /api/ke/groups` | 各组今日上传状态 / 最近批次 |
| GOLD 成品 | `GET/POST /api/gold/items` · `GET/PATCH/DELETE /api/gold/items/{id}` · `POST /api/gold/items/{id}/status` | CRUD + 状态流转（draft→reviewed→gold；→gold 有 KE 结构强校验，422 阻断） |
| | `GET /api/gold/items/export.csv` / `.json` / `.manifest.json` | 导出（过滤参数同列表：article_code/status/group_name/q）；manifest = 逐条 sha256 + 整体 digest（数据版本守卫） |
| | `POST /api/gold/items/{id}/validate` · `POST /api/gold/items/validate-all` | M20 KE 校验：单条问题明细 / 批量重算（reviewer/admin） |
| GOLD 管理 | `/api/gold/articles` · `/api/gold/schedule` · `/api/gold/issues` · `/api/gold/samples` · `/api/gold/stats` | 篇任务 / 泳道 / 分歧 / 抽查 / 汇总 |
| | `POST /api/gold/samples/auto` · `GET /api/gold/samples/{id}` · `POST …/{id}/reviews` · `POST …/{id}/items/{gid}/arbitrate` · `POST …/{id}/close` | M21 抽检闭环：分层等距抽样（seed 可复现）→ 双人盲评（重复打标 409）→ 分歧仲裁 → 关闭回填通过率（Wilson 95% CI + 一致率） |
| KE 复核 | `GET /api/ke/review/queue` · `POST /api/ke/review` · `POST /api/ke/review/batch` · `GET /api/ke/review/history` | 待复核队列 / 逐条打标（写回任务并留痕）/ 批量 / 留痕查询（仅 reviewer/admin） |
| 用户与组 | `GET/POST /api/admin/users` · `PATCH /api/admin/users/{uid}` · `POST /api/admin/users/{uid}/password` | 建号 / 改角色与组 / 重置密码（仅 admin） |
| 工作流 | `docs/oms-workflow.md`（OMS 当前 As-Is 流程 + 实现边界） | 标注/规则/个体/KEOL 同步 |
| 本体 | `/api/concepts` · `/api/operators`（含 `inputs`/`signature`/`output_concept`） · `/api/submissions`（含 approve/reject/merge/batch）· `/api/tmp` · `/api/health` · `/api/io` · `/api/export/json`（含 rules/individuals） | 查重 / 审核 / 回填 / 体检 / 导入导出 |
| 标注 API | `/api/annotation/batches` · `…/units` · `…/state` · `…/apply` · `…/export` · `/api/annotation/units/{uid}/{co,align,ke,gold,run-query}` · `/api/annotation/versions` · `…/payload` · `…/diff` · `…/rollback` | 批次/单元/CO/对齐/KE/GOLD/版本全流程 |
| 规则/个体 | `/api/rules` · `/api/individuals`（GET/POST/PATCH） · `/api/export/portable` · `scripts/remap_rule_refs.py` | 当前库规则/个体 CRUD / 跨实例迁移 |
| 本体库 | `GET/POST /api/libraries` · `/api/libraries/{key}/members` | 库注册表/成员权限；无成员不可访问；页面 `/libraries` |
| 概念编号 | 概念 API 的 `code` / `local_code` | 库内编号如 `ARCH-0001`；全局 `id` 仍用于 URL/外键 |
| 描述 | 概念/算子 API 的 `description` | 全量已有；Archer 概念按来源库/字段生成 |
| NL2KE | `/nl2ke` · `/api/nl2ke/databases` · `/api/nl2ke/run` · `/api/nl2ke/runs` | Archer 单库 PoC；BYOS token 仅服务端 |
| 搜索 | `/api/search?q=&types=&limit=` | 跨概念/算子/规则/个体搜索 |
| 运行 | `/api/ops/summary` · `/ops` | DB/备份/证书/Token/端口状态（admin） |
| 本体建议 | `/api/keol/suggestions`（GET/POST，detail/approve/reject）· 页面 `/suggestions` | KEOL/agent 建议审批 |
| KEOL 库 | `GET /api/keol/status` · `/api/keol/entities` · `/api/keol/entities/{name}` · `/api/keol/facts` | M25：KEOL 实例/事实镜像只读检索（`scripts/sync_keol.py --with-instances` 灌入；页面 `/keol`） |
| 变更 | `/api/history`（历史）· `/api/concepts/{id}/rollback/{audit_id}`（回滚，admin） | 全部写操作留痕可回滚 |
| 认证 | `POST /api/auth/login` | 服务间登录校验（KEOL 控制台复用 OMS 账号） |
| Token | `POST/GET/DELETE /api/tokens` | 签发（代签需 admin）/ 列表 / 吊销 |

## 3. Agent 开放度（2026-10-28 实测）

| 能力面 | 开放度 | 入口 |
|---|---|---|
| 概念 / 算子 / 查重 / 提交 / TMP / 体检 / GOLD / KE / Issue / 题目 / 用户 | 高 | `/api/*` REST + MCP 工具 |
| 全局导入导出（概念/算子/规则/个体） | 高（需 admin） | `GET /api/export/json`、`POST /api/import/json` |
| 全局规则 / 个体 | 高 | `/api/rules`、`/api/individuals` CRUD + MCP |
| 标注批次 / 单元 / CO / 对齐 / KE / GOLD / 版本 | 高 | `/api/annotation/*` JSON API + MCP；页面 `/annotation/*` 仍保留 |
| KEOL 桥 | 独立系统 | KEOL 控制台 `/api/oms/*`；OMS 不内置拉取/推送 API；控制台使用 OMS 账号登录（8444 TLS） |

可发现性：

- `GET /api/agent/manifest`：当前实测 162 个 `/api/` 端点、6 条工作流配方；含 `annotation`、`rules`、`individuals` API。
- `GET /openapi.json`：当前 265 个路径；标注/规则/个体 API 有 Pydantic 请求模型，页面路径仍保留。
- MCP：83 个工具，stdio 传输；包含标注工作流、版本快照/diff/rollback、规则/个体 CRUD、建议箱、全局搜索、可移植导出；不含 KEOL 推送/拉取。

认证与治理：

- 登录用户可为**自己**签发 Bearer Token（默认 30 天过期，`expires_in_days=0` 可不过期）；admin 可代签；token 明文只返回一次，可吊销，有 `read`/`write` 两个 scope；按 token 每分钟限流，审计写入 token_name。
- 权限仍按用户角色判定；Bearer 调用免 CSRF；token 无过期时间、无细粒度 scope、无速率限制/配额。
- 写操作走审计（actor 是用户名；token 名不进入审计），但多个 agent 共用同一 token 时无法区分。
- 因此：读/查询、本体 CRUD、GOLD/KE/Issue、标注批次/版本、规则/个体都适合 agent 直接接；KEOL 拉取/推送仍通过 KEOL 控制台。

## 4. 约定

- **页面/API 对等范围**：M12 历史页面已审计；M35 已补标注工作流与规则/个体 API（见 §3）
- **错误**：统一 `{"detail": "…"}` + 恰当 HTTP 码（400 参数 / 401 未认证 / 403 越权 / 404 不存在 / 409 幂等冲突 / 422 校验）
- **认证细节**：Bearer 优先于并存的 session cookie（显式凭证为准，防提权）；只读 token（`scopes=["read"]`）调写接口 → 403；`last_used_at` 按 `TOKEN_LAST_USED_THROTTLE`（默认 300s）节流写库
- **幂等键**：KE 上传 =（组， 题目ID）；成品 =（篇编号， KE编号）；概念/算子 = 名称自然键；规则 = 名称；个体 =（所属概念, 名称）；复核结论 =（组， 题目ID）取最新一条；重复提交返回 409 而非重复创建
- **复核权威**：系统复核结论为准；组内 CSV 自报在下次上传时被自动重放覆盖
- **CSV 列名**：默认中文十列 + 可选「篇编号」「KE内容」；别家表头用环境变量 `KE_COLUMN_MAP='{"id":"题目编号",...}'` 单项覆盖即可，无需改代码
- **TMP 回填**：仅 resolved / merged 的 TMP 会被替换为正式概念名；其余列入未决清单（`GET /api/tmp-mappings` 可查）

## 5. 通用性（别家工况）

- **账号与组**：组名取自账号体系（admin 在 `/admin/users` 或 `/api/admin/users` 建号分组），不写死
- **词表全部可配**：GOLD 阶段流水线（`GOLD_STAGES`）、泳道时段（`GOLD_SLOTS`）、KE 状态写法（`KE_STATUS_MAP`）、KE 表头（`KE_COLUMN_MAP`）、目标口径（`GOLD_BASELINE` / `GOLD_TARGET_TOTAL` / `GOLD_TARGET_NEW`）、查重阈值、体检阈值与 cron——均为环境变量，见 `.env.example`
- **KEOL 桥接**：`KEOL_SRC` 指定政府本体库源目录（`scripts/sync_keol.py` 词表+实例镜像 / `scripts/gold_to_keol.py` 成品回流的默认 `--src`）
- **种子数据可关**：`SEED_DEMO=0`（演示概念/算子/GOLD 预置）、`SEED_KE=0`（KE 演示数据）；别的团队干净起步 → 建号 → `/api/import/json` 导自己本体
- **数据库**：默认 SQLite（`data/ontology.db`）；切 PostgreSQL 只换 `DB_URL`

## 6. MCP server（agent 直连）

```jsonc
// .mcp.json 接入示例
{"mcpServers": {"ontology": {"command": "python", "args": ["scripts/mcp_server.py"],
  "env": {"ONTOLOGY_BASE_URL": "http://127.0.0.1:8000", "ONTOLOGY_TOKEN": "ke_..."}}}}
```

83 个工具覆盖：whoami / get_manifest / 概念与算子 CRUD / 查重 / 提交审核 / TMP 回填 / GOLDKE 篇级与泳道 / issue 话题评论与正式 Issue 闭环 / KE 上传与组别状态 / 成品校验与指纹 manifest / 抽检闭环（抽样-盲评-仲裁-关闭）/ 体检 / 导出与变更日志 / LLM 台账 / 标注全流程 / 规则/个体 CRUD / 建议箱审批 / 全局搜索 / 版本快照 / 可移植导出。工具即 §2 端点的薄封装，错误同样返回 `{error, detail}`。
