# OMS × NL2KE

## NL2KE 仓库状态

- 主仓库：`/home/oowpc/NL2KE`
- 分支：`main`
- HEAD：`27f4c33`（`origin/main` 同提交；`git fetch` 无更新）
- 本地差异：仅 `.gitignore`、`src/nl2ke/config/config.py` 的既有改动，没有本轮代码修改
- 另一个 `/home/oowpc/fhNL2KE` 是老分叉（`0b66f7b`），不用于本次集成

## 当前 PoC

```text
OMS /nl2ke
  → 选择 Archer 数据库 + 自然语言问题
  → OMS 组装 tables_json + 该库子本体
  → NL2KE BYOS POST /v1/ke
  → 返回 KE
  → OMS 写入 nl2ke_runs 历史
```

- NL2KE 最新服务：`http://127.0.0.1:18004`
- 模型档：`qwen3_coder_flash`
- OMS 页面：`/nl2ke`
- KE→SQL：ke2sql **v0.3.1**，`127.0.0.1:8081`
- Calcite：Docker `ke2sql-calcite-8091`，`127.0.0.1:8091`
- OMS 环境：`KE2SQL_BASE_URL=http://127.0.0.1:8081`
- OMS API：
  - `GET /api/nl2ke/databases`
  - `POST /api/nl2ke/run`
  - `POST /api/nl2ke/batch`
  - `GET /api/nl2ke/runs`
  - `POST /api/nl2ke/runs/{id}/accept` → GOLD 草稿
  - `POST /api/nl2ke/runs/{id}/reject`
- 运行记录表：`nl2ke_runs`，按 OMS 库隔离
- P0：
  - 接受结果创建 `gold_items` 草稿（`article_code=NL2KE`）
  - 也可加入标注批次/单元；自动批次 `NL2KE-{db_id}`，单元 `NL2KE-{run_id}`，KE 存在 `source_payload`
- P1：`concepts.source_payload` / `operators.source_payload` + `ontology_sources` 已保存 Archer 结构化来源；桥优先从 OMS 读取
- P2：`nl2ke_runs.batch_tag` 支持一次多问题串行运行

## 服务配置

- OMS `.env`：`NL2KE_BASE_URL`、`NL2KE_TOKEN`、`NL2KE_AGENT`、`NL2KE_ARCHER_SRC`
- NL2KE systemd：`~/.config/nl2ke-api.env` 提供 `NL2KE_BYOS_API_TOKEN`、`QWENKEY`、`QWENBASEURL`
- NL2KE `/v1/ke` 仍使用 Bearer；OMS 前端拿不到 token

## Archer SQLite

10 个 Archer benchmark 数据库已整理到：

```text
data/archer-dbs/
```

- 9 个原生 SQLite
- `soccer_1.sqlite` 使用 `Spider2/.../EU_soccer.sqlite` 替代（6 张核心表一致，额外有 Match 表）
- `manifest.json` 记录 db_id、来源、大小
- `ontology_sources.sqlite_path` 已注册这 10 个库
- 页面数据库下拉会显示 `· SQLite`
- 批量区可「载入该库 Archer 题目」，题目来自 `en_dev/zh_dev/train_ke_sql`

## NL2KE 内部 trace

OMS 调 NL2KE 时固定发送 `debug: true`，BYOS 返回并保存 `trace`：

- `schema`：实际 schema 文本与长度
- `ontology`：精炼前/后的概念、算子名单和数量
- `meta`：问题元信息提取结果
- `fewshot`：命中的示例题目/KE/SQL
- `verify_loop`：每次 attempt 的 KE、本体规则校验、语义校验、反构 NL、失败原因
- `prompt`：初始生成 prompt
- `timings`：meta/refine/fewshot/verify_loop 分段耗时

每个步骤是独立页面：

```text
/nl2ke/runs/{id}
/nl2ke/runs/{id}/steps/schema
/nl2ke/runs/{id}/steps/meta
/nl2ke/runs/{id}/steps/ontology
/nl2ke/runs/{id}/steps/fewshot
/nl2ke/runs/{id}/steps/attempts
/nl2ke/runs/{id}/steps/prompt
/nl2ke/runs/{id}/steps/timings
```

`/nl2ke` 历史运行列表点「查看」进入概览；每页有步骤导航和上一步/下一步。
数据存放在 `nl2ke_runs.result_payload.trace`，不新增数据库字段。`trace` 只在
`debug=true` 时返回；普通调用仍保持原响应大小。

## KE 等价校验（可选）

单条运行可接 KE-matcher 做参考 KE 对比：

```text
/nl2ke 结果面板
  → 粘贴参考 KE（gold / 人工确认）
  → POST /api/nl2ke/runs/{id}/equivalence
  → OMS → KE-matcher POST /judge
  → proved_equal / proved_different / unknown
```

- KE-matcher：`/home/oowpc/KE-matcher`，服务 `127.0.0.1:8082`
- systemd：`~/.config/systemd/user/ke-matcher.service`
- 独立运行环境：`/home/oowpc/KE-matcher/.venv-service`（Python 3.14）
- 默认层：S1 形式校验/表面归一化，`keval_path` 指向
  `/home/oowpc/Syntastic_and_Lexical-Verifier`
- OMS 环境：`KE_MATCHER_BASE_URL`、`KE_MATCHER_KEVAL_PATH`、`KE_MATCHER_TIMEOUT`
- 参考 KE 只发给 KE-matcher，不会写入 NL2KE 请求；校验结果保存在
  `nl2ke_runs.result_payload.equivalence`
- 参考 KE 手工提供；不自动从 Archer 题库带出，也不做批量评测
- 未配置 RA/Z3 registry 时，只能判表面等价，语义等价可能返回 `unknown`

## 当前限制

- 桥已优先从 OMS `source_payload` / `ontology_sources` 组装；源 JSON 只作兼容回退
- 只支持 Archer 库；其他库需要 schema 与 source mapping
- 接受结果可创建 GOLD 草稿或加入草稿标注批次，二者是独立记录
- 206 个 schema 仍不都有 SQLite；Archer benchmark 10 个库已有 9 个原生 + 1 个替代
- 已接 KE→SQL 与只读执行；还没有把执行结果与 Archer gold SQL 自动对比
- P4 反向本体建议尚未自动生成；NL2KE 当前只消费本体，不产出可直接审批的建议包
- NL2KE 单进程有全局模型档锁，不适合高并发；需要多进程/多实例
