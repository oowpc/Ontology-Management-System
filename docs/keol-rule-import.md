# KEOL rule 推送与版本纳入

## 目标

把 KEOL 当前工作本体（概念/算子/规则）推到 OMS 独立库，先生成建议包，再由
reviewer/admin 审批，最后纳入本体版本。

## 本次实际执行

- 目标库：`keol-yuer`（reference，避免污染 `pilot-yuer`）
- 来源：`/home/oowpc/KEOL/console/data/ontology.json`
- 推送：KEOL `POST /api/oms/push`，`mode=suggest`，`library=keol-yuer`
- 建议包：322 概念 / 224 算子 / 16 规则
- OMS 审批：`/api/keol/suggestions/1/approve`
- 结果：`ontology_rules` 16 条，全部通过统一 rule 校验
- 版本：批次 `KEOL-RULES-20261103`，版本 `KEOL-R1`
- 快照 counts：`{concepts: 322, operators: 224, rules: 16, individuals: 0}`
- 状态：`frozen`

## 复现

1. OMS 创建目标库：

```http
POST /api/libraries
{"key":"keol-yuer","name":"KEOL 育儿补贴参考本体","kind":"reference"}
```

2. KEOL 推送建议：

```http
POST http://127.0.0.1:8321/api/oms/push
Authorization: Bearer <KEOL_CONSOLE_TOKEN>
{"mode":"suggest","confirm":true,"library":"keol-yuer","domain":"KEOL"}
```

3. OMS 审批建议：

```http
POST /api/keol/suggestions/{id}/approve
Authorization: Bearer <OMS_TOKEN>
X-Ontology-Library: keol-yuer
```

4. 创建批次与版本：

```http
POST /api/annotation/batches
X-Ontology-Library: keol-yuer
{"batch_id":"KEOL-RULES-...","name":"KEOL rule 推送与版本纳入"}

POST /api/annotation/batches/{id}/versions
{"label":"KEOL-R1"}
```

## 持续同步

- `/keol/sync` 检查 `KEOL_ONTOLOGY_PATH` 的源 hash 与当前库差异。
- 检查只记录 `keol_sync_runs`，不自动改对象。
- 可通过 `KEOL_SYNC_CRON` 让 OMS 定时检查；推送仍走 KEOL `/api/oms/push` 建议包 + OMS 审批。
- `POST /api/keol/sync/apply`（需 `KEOL_CONSOLE_TOKEN`）只提交建议，不直接写规则。
- CLI：`.venv/bin/python scripts/check_keol_sync.py --library keol-yuer`

## 注意

- 建议审批走审核链路，不直接改正式库。
- 版本冻结前必须 `validation.ok=true`。
- KEOL 229 个算子中 5 个因缺输入主体被桥接层跳过，版本实际 224 个。
