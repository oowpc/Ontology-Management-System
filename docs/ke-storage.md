# 本体与 KE 存储

## 本体

主存储：

```text
data/ontology.db
```

按库隔离：

- `ontology_libraries`
- `concepts` / `concept_parents`
- `operators` / `operator_inputs`
- `ontology_rules`
- `ontology_individuals`
- `ontology_versions`（完整 payload 快照）
- `ontology_suggestions`

KEOL 源本体仍独立保存在：

```text
/home/oowpc/KEOL/console/data/ontology.json
```

`keol-yuer` 是 KEOL 当前工作本体的 OMS 镜像。

## KE 文档层

统一索引表：

- `ke_documents`
  - `library_id`
  - `source_type`：`gold / datamaker / nl2ke / annotation / manual`
  - `source_ref`
  - `question`
  - `ke_text`
  - `content_hash`
  - `status`：`candidate / reviewed / gold / rejected`
  - `ontology_version_id`
  - `gold_item_id`
  - `validation`
- `ke_document_versions`
  - 每次 KE 文本变化追加一条版本

人工标注通过：

- `ke_annotations.ke_document_id`
- `gold_kes.ke_document_id`

GOLD 和 NL2KE 分别通过：

- `gold_items.ke_document_id`
- `nl2ke_runs.ke_document_id`

关联到统一文档。

## 原始来源仍保留

统一文档不替代原始记录：

- `gold_items.ke_text`
- `nl2ke_runs.ke` / `result_payload`
- `annotation_units.source_payload.ke`
- `ke_annotations.content`
- `gold_kes.content`
- `data/datamaker/job-*/ke1.jsonl`

它们是证据/来源；`ke_documents` 是统一索引和版本层。

## 回填

```bash
.venv/bin/python scripts/backfill_ke_documents.py
```

回填后文档按 `annotation_batch_id` 关联批次；创建本体版本时，批次下文档的
`ontology_version_id` 会写入对应版本。

当前回填：32 个文档；GOLD 10 条、NL2KE 15 条、DataMaker 8 条、标注 9 条。

## API / 页面

- `/ke/documents`
- `GET /api/ke/documents`
- `GET /api/ke/documents/{id}`：包含版本历史
