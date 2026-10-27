# Agent 操作手册（本体管理系统 / GOLDKE）

本系统对 agent **完全开放**：页面上能做的，API 都能做。认证一次，全流程可编程操控。

> 首次接入先做两件事：① 按 §1 拿 Token；② `GET /api/agent/manifest` 拿活清单（全部路由 + 当前工况词表 + 工作流配方，运行时生成，永不缺漏）。

## 0. 活清单（推荐入口）

```bash
curl -H "Authorization: Bearer ke_..." http://127.0.0.1:8000/api/agent/manifest
```

返回：按域分组的全部 API 路由、当前工况词表（GOLD 阶段/泳道时段/组别/目标、KE 状态词表与列名映射、查重阈值、LLM 是否可用）、常用工作流、幂等键与错误约定。另有 MCP server（`scripts/mcp_server.py`，47 个工具，见 §5）。

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
| 本体 | `/api/concepts` · `/api/operators` · `/api/submissions`（含 approve/reject/merge/batch）· `/api/tmp` · `/api/health` · `/api/io` | 查重 / 审核 / 回填 / 体检 / 导入导出 |
| KEOL 库 | `GET /api/keol/status` · `/api/keol/entities` · `/api/keol/entities/{name}` · `/api/keol/facts` | M25：KEOL 实例/事实镜像只读检索（`scripts/sync_keol.py --with-instances` 灌入；页面 `/keol`） |
| 变更 | `/api/history`（历史）· `/api/concepts/{id}/rollback/{audit_id}`（回滚，admin） | 全部写操作留痕可回滚 |
| Token | `POST/GET/DELETE /api/tokens` | 签发（代签需 admin）/ 列表 / 吊销 |

## 3. 约定

- **页面/API 对等已审计**：全部页面写操作都有对应 API
- **错误**：统一 `{"detail": "…"}` + 恰当 HTTP 码（400 参数 / 401 未认证 / 403 越权 / 404 不存在 / 409 幂等冲突 / 422 校验）
- **认证细节**：Bearer 优先于并存的 session cookie（显式凭证为准，防提权）；只读 token（`scopes=["read"]`）调写接口 → 403；`last_used_at` 按 `TOKEN_LAST_USED_THROTTLE`（默认 300s）节流写库
- **幂等键**：KE 上传 =（组， 题目ID）；成品 =（篇编号， KE编号）；概念/算子 = 名称自然键；复核结论 =（组， 题目ID）取最新一条；重复提交返回 409 而非重复创建
- **复核权威**：系统复核结论为准；组内 CSV 自报在下次上传时被自动重放覆盖
- **CSV 列名**：默认中文十列 + 可选「篇编号」「KE内容」；别家表头用环境变量 `KE_COLUMN_MAP='{"id":"题目编号",...}'` 单项覆盖即可，无需改代码
- **TMP 回填**：仅 resolved / merged 的 TMP 会被替换为正式概念名；其余列入未决清单（`GET /api/tmp-mappings` 可查）

## 4. 通用性（别家工况）

- **账号与组**：组名取自账号体系（admin 在 `/admin/users` 或 `/api/admin/users` 建号分组），不写死
- **词表全部可配**：GOLD 阶段流水线（`GOLD_STAGES`）、泳道时段（`GOLD_SLOTS`）、KE 状态写法（`KE_STATUS_MAP`）、KE 表头（`KE_COLUMN_MAP`）、目标口径（`GOLD_BASELINE` / `GOLD_TARGET_TOTAL` / `GOLD_TARGET_NEW`）、查重阈值、体检阈值与 cron——均为环境变量，见 `.env.example`
- **KEOL 桥接**：`KEOL_SRC` 指定政府本体库源目录（`scripts/sync_keol.py` 词表+实例镜像 / `scripts/gold_to_keol.py` 成品回流的默认 `--src`）
- **种子数据可关**：`SEED_DEMO=0`（演示概念/算子/GOLD 预置）、`SEED_KE=0`（KE 演示数据）；别的团队干净起步 → 建号 → `/api/import/json` 导自己本体
- **数据库**：默认 SQLite（`data/ontology.db`）；切 PostgreSQL 只换 `DB_URL`

## 5. MCP server（agent 直连）

```jsonc
// .mcp.json 接入示例
{"mcpServers": {"ontology": {"command": "python", "args": ["scripts/mcp_server.py"],
  "env": {"ONTOLOGY_BASE_URL": "http://127.0.0.1:8000", "ONTOLOGY_TOKEN": "ke_..."}}}}
```

47 个工具覆盖：whoami / get_manifest / 概念与算子 CRUD / 查重 / 提交审核 / TMP 回填 / GOLDKE 篇级与泳道 / issue 话题评论与正式 Issue 闭环 / KE 上传与组别状态 / 成品校验与指纹 manifest / 抽检闭环（抽样-盲评-仲裁-关闭）/ 体检 / 导出与变更日志 / LLM 台账。工具即 §2 端点的薄封装，错误同样返回 `{error, detail}`。
