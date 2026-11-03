# OMS × DataMaker

## 入口

`/datamaker`：选择 Archer 数据库、模型、题量、KE 类型、并发，后台启动 datamaker。

任务页：

- `/datamaker`
- `/datamaker/jobs/{id}`

## 流程

```text
OMS 当前 Archer 库
  → 读取 OntologySource.sqlite_path
  → 从 Concept/Operator.source_payload 导出 registry.json
  → datamaker gen_ke.py（后台 subprocess）
  → 读取 workdir/ke1.jsonl
  → 创建 draft AnnotationBatch + AnnotationUnit
```

- 生成结果不会直接写入正式 GOLD
- 草稿批次名称：`DataMaker #{job_id}`
- 每条单元 `raw_text = question`，`source_payload` 保存 KE/evidence/type/tags/job_id

## 任务与产物

- 表：`datamaker_jobs`
- 工作目录：`data/datamaker/job-{id}/`
- 产物：`ke1.jsonl`、`registry.json`、`spec.json`、`coverage.json`、`run_manifest.jsonl`、`job.log`
- 页面可下载产物、查看日志、取消任务
- OMS 重启会把未完成且不在当前进程 active 集合内的任务标记为 `interrupted`

## 配置

`.env`：

```text
DATAMAKER_DIR=/home/oowpc/datamaker
DATAMAKER_PYTHON=/home/oowpc/datamaker/.venv/bin/python
DATAMAKER_WORK_ROOT=/home/oowpc/Ontology-Management-System/data/datamaker
DATAMAKER_LLM_BASE_URL=...
DATAMAKER_LLM_API_KEY=...
DATAMAKER_LLM_MODEL=qwen3-coder-flash
DATAMAKER_MODELS=qwen3-coder-flash
DATAMAKER_KE2SQL_PATH=/home/oowpc/ke2sql-v031
DATAMAKER_VERIFIER_PATH=/home/oowpc/Syntastic_and_Lexical-Verifier
DATAMAKER_MAX_ACTIVE=1
DATAMAKER_TIMEOUT=7200
```

`DATAMAKER_PYTHON` 使用 datamaker 自己的 venv；当前 venv 需要 `lark` 和 `sqlglot`。

## 权限

- 仅 Archer 库
- 只有 reviewer/admin 可以启动和取消
- 只允许配置白名单模型
- 仅允许 OMS 已注册的 SQLite

## 当前限制

- 只接 `gen-ke`，不是 roundtrip/NLI/三方互比
- 生成问题是 datamaker 的合成候选，不是人工审核数据
- 同一库默认最多 1 个运行中任务
- 不自动创建正式 GOLD
- DataMaker 执行已从 Web 进程拆到 `oms-datamaker-worker.service`
- Web 只入队；worker 取 `queued` 任务执行
- worker 重启会把遗留 `running` 标记为 interrupted

## P1：重试、产物和质量指标

- 任务结束后可在详情页「重试」；会创建新任务，不复用旧 workdir
- 取消：运行中任务发送 `POST /api/datamaker/jobs/{id}/cancel`
- 产物下载：`ke1.jsonl`、`registry.json`、`spec.json`、`coverage.json`、`run_manifest.jsonl`、`job.log`
- 任务列表显示：任务总数、成功/失败、生成条数、导入条数、任务成功率
- 任务详情显示：
  - 完成率
  - 导入单元数
  - 本体算子覆盖
  - 内置算子覆盖
  - KE 类型分布
  - 未覆盖概念

重试只允许已结束任务；生产默认每次重试生成独立任务，便于对比。

## P2a：roundtrip / tri

任务模式：

- `gen-ke`：只生成 KE
- `roundtrip`：`roundtrip_pipeline.py all`，KE→SQL→NL→KE→等价→NLI
- `tri`：KE1/KE2/KE3 三方回环和等价分类

新增参数：

- `nl_source`：`ke` / `sql`
- `cross_check`
- `service_url`：必须为本机 ke2sql 服务
- `timeout_s`
- `DATAMAKER_Z3_PATH`

产物新增：

- `equiv.jsonl` / `nli.jsonl` / `report.json`
- `equiv_tri.jsonl` / `report_tri.json` / `report_tri.md`
- `sql.jsonl` / `nl2.jsonl` / `ke2.jsonl` / `ke3.jsonl`

质量指标新增：

- 等价对数、pass/mismatch
- NLI paraphrase/neutral
- tri identical / sem_eq / sem_diff
- roundtrip/tri 仍只导入 `ke1.jsonl` 到草稿批次
- 任务成功后默认同时把 `ke1.jsonl` 建成 GOLD 草稿（`DATAMAKER_AUTO_GOLD=1`）：
  `article_code=DM-<job>`、`ke_id=DM-<job>-NNNN`、`status=draft`；只进入人工审查，
  不自动 reviewed/gold；每条都走统一 `validate_ke` 并留校验记录
- 任务页展示「GOLD 草稿 N」并跳到 `/gold/items?q=DM-<job>`
- 历史任务回填：`.venv/bin/python scripts/backfill_datamaker_gold.py`

安全处理：

- `roundtrip_pipeline.py` 不再把 LLM key 写入命令行日志
- OMS 读取日志时会再脱敏 `--key` 和 `sk-*`


## P2b：多库与 ontology-only

- 非 Archer 库不再要求 SQLite，任务以 `db_id=__ontology__` 运行
- 本体从 OMS 当前库导出，生成 ontology-only registry
- 概念/算子名若不是 ASCII，会映射为 `Concept{id}` / `Operator{id}`
- 映射写入 `name_map.json` 产物；批次中的 KE 使用映射后的名字
- 只有当前库的 `OntologySource` 才允许走 SQLite 完整模式
- 没有 SQLite 时跳过执行级校验；roundtrip/tri 的等价/NLI 结果可能为空

## Worker claim/lease

- `datamaker_jobs` 增加 `worker_id`、`heartbeat_at`
- worker 用条件 UPDATE 原子 claim `queued` 任务
- 执行期间定期刷新 heartbeat
- worker 启动和每 60 秒清理 heartbeat 超时（默认 300 秒）的 `running`
- 多 worker 不会同时执行同一任务；活跃 worker 的任务不会被误杀

## GOLD 质量门禁

DataMaker 任务成功后，OMS 会按以下顺序决定是否自动建 GOLD 草稿：

1. `DATAMAKER_AUTO_GOLD=0`：跳过。
2. `DATAMAKER_GOLD_GATE_MODE=auto`（默认）：
   - 若 job workdir 存在 `gold_eval.json`，读取其 `gate`；
   - `gate.passed=false` 时不建 GOLD 草稿，并把快照写入 `datamaker_jobs.gold_gate`。
   - 没有报告时保持旧行为，允许建草稿。
3. `require`：没有质量报告也阻断。
4. `off`：完全跳过质量门禁。

生成质量报告：

```bash
python eval_gold.py --ke <job>/ke1.jsonl --gold <gold>.jsonl \
  --db <db> --ontology <ontology> --ke-path <ke2sql> \
  -o <job>/gold_eval.json --min-exec-rate 0.9 --min-accuracy 0.8
```

任务页展示「质量门禁」和 `gold_eval.json` 的指标。

## 成本摘要

任务页/数据生成页汇总 `report.json` / `report_tri.json` 中的
`cost.calls / prompt_tokens / completion_tokens / usd`。

## 原生 Job API 模式

OMS 设置以下变量后，新建 DataMaker 任务会提交到 DataMaker 自己的 API：

```env
DATAMAKER_NATIVE_API_URL=http://127.0.0.1:8093
DATAMAKER_NATIVE_API_TOKEN=
```

- OMS 保存本地任务记录和 `native_job_id`
- OMS worker 跳过 native 任务
- OMS 页面/列表轮询原生 API，更新状态和进度
- 原生产出成功后由 OMS 执行批次导入和 GOLD 门禁
- DataMaker API/worker 独立运行，可启动多个 `datamaker-worker@N`
