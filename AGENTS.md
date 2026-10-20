# Agent 操作手册（本体管理系统 / GOLDKE）

本系统对 agent **完全开放**：页面上能做的，API 都能做。认证一次，全流程可编程操控。

## 1. 认证

```bash
# 方式一：用账号密码登录拿 session，再自助签发 Token（推荐）
curl -c ck.txt -d "username=annotator&password=annotator" http://127.0.0.1:8000/login
curl -b ck.txt -X POST http://127.0.0.1:8000/api/tokens -H "Content-Type: application/json" -d '{"name":"my-agent"}'
# → {"token": "ke_..."}   ⚠ 明文仅此一次返回

# 方式二：请管理员代签（admin 可为任意用户签发）
```

之后所有请求：`Authorization: Bearer ke_...`；吊销：`DELETE /api/tokens/{id}`。
自检：`GET /api/me`。在线文档：`/docs`（OpenAPI）或 `/openapi.json`。

## 2. 端点速查

| 域 | 端点 | 说明 |
| --- | --- | --- |
| KE 进度 | `POST /api/ke/upload` | multipart CSV **全量上传**（幂等：按 组+题目ID 覆盖；带「篇编号+KE内容」列会同步入成品库草稿） |
| | `GET /api/ke/groups` | 各组今日上传状态 / 最近批次 |
| GOLD 成品 | `GET/POST /api/gold/items` · `GET/PATCH/DELETE /api/gold/items/{id}` · `POST /api/gold/items/{id}/status` | CRUD + 状态流转（draft→reviewed→gold） |
| | `GET /api/gold/items/export.csv` / `.json` | 导出（过滤参数同列表：article_code/status/group_name/q） |
| GOLD 管理 | `/api/gold/articles` · `/api/gold/schedule` · `/api/gold/issues` · `/api/gold/samples` · `/api/gold/stats` | 篇任务 / 泳道 / 分歧 / 抽查 / 汇总 |
| KE 复核 | `GET /api/ke/review/queue` · `POST /api/ke/review` · `POST /api/ke/review/batch` · `GET /api/ke/review/history` | 待复核队列 / 逐条打标（写回任务并留痕）/ 批量 / 留痕查询（仅 reviewer/admin） |
| 用户与组 | `GET/POST /api/admin/users` · `PATCH /api/admin/users/{uid}` · `POST /api/admin/users/{uid}/password` | 建号 / 改角色与组 / 重置密码（仅 admin） |
| 本体 | `/api/concepts` · `/api/operators` · `/api/submissions`（含 approve/reject/merge/batch）· `/api/tmp` · `/api/health` · `/api/io` | 查重 / 审核 / 回填 / 体检 / 导入导出 |
| 变更 | `/api/audit`（历史、回滚） | 全部写操作留痕可回滚 |
| Token | `POST/GET/DELETE /api/tokens` | 签发（代签需 admin）/ 列表 / 吊销 |

## 3. 约定

- **页面/API 对等已审计**：全部 32 个页面写操作都有对应 API（2026-10 审计：140 条路由 / 59 条 API）
- **错误**：统一 `{"detail": "…"}` + 恰当 HTTP 码（400 参数 / 401 未认证 / 403 越权 / 404 不存在 / 409 幂等冲突）
- **幂等键**：KE 上传 =（组， 题目ID）；成品 =（篇编号， KE编号）；概念/算子 = 名称自然键；复核结论 =（组， 题目ID）取最新一条；重复提交返回 409 而非重复创建
- **复核权威**：系统复核结论为准；组内 CSV 自报在下次上传时被自动重放覆盖
- **CSV 列名**：默认中文十列 + 可选「篇编号」「KE内容」；别家表头用环境变量 `KE_COLUMN_MAP='{"id":"题目编号",...}'` 单项覆盖即可，无需改代码
- **词表可配**：`KE_STATUS_MAP`（状态写法映射，如 `{"done":["done","完成"]}`）、`GOLD_STAGES`（篇级阶段流水线，建库前设定）、`GOLD_SLOTS`（泳道时段）
- **定稿保护**：reviewed / gold 状态的成品条目不会被上传或标注员改动覆盖；调整需 reviewer/admin，全部留痕
