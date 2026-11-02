# OMS 本体数据基线

> 整理时间：2026-10-28  
> 目标：生产 OMS 只保留 **FIBO/CMNS 导入本体 + 明确的 CO v0.8 业务基线**；清理测试数据。  
> 当前生产库：`data/ontology.db`；整理前备份：`data/backups/ontology-20261028-024755-pre-baseline.db`。M44 起为多本体库（FIBO/CMNS 参考库、CO 业务基线、Pilot 库）。

## 1. 当前构成

| 层 | 概念 | 算子 | 标识 |
|---|---:|---:|---|
| FIBO | 2992 | 3385（与 CMNS 合计） | `domain='FIBO'`；有 `source_uri` / `ontology_module` |
| CMNS | 158 | 同上 | `domain='CMNS-*'`；19 个模块 |
| **FIBO/CMNS 小计** | **3150** | **3385** | 导入批次 `prod-20261027-1000` |
| CO v0.8 业务基线 | **20** | **5** | `domain='业务基线'`、`ontology_module='CO-v0.8'`、`source_uri='internal:co-v0.8'` |
| 合计 | **3170** | **3390** | 已按库拆分，见 `docs/ontology-libraries.md` |
| 库 | `fibo-cmns` 3150/3385；`co-baseline` 20/5；`pilot-yuer` 0/0 | — | `ontology_libraries` |
| 全局规则 | 0 | — | `ontology_rules` |
| 全局个体 | 0 | — | `ontology_individuals` |
| 标注批次 | 2（v1 `PILOT-20261028-01` active；v2 返工审计） | — | `annotation_batches` |

关系：4431 条 `concept_parents`；`submissions` / `tmp_mappings` / `backfill_records` 均为 0（测试链已清理）。

### 1.1 业务基线内容

20 个概念：

`机构`、`行政区划`、`人`、`职务`、`民族`、`职责`、`国家机构`、`党的机构`、`基层群众性自治组织`、`行政机关`、`权力机关`、`监察机关`、`检察机关`、`审判机关`、`地方政府`、`省级行政区`、`地级行政区`、`县级行政区`、`乡级行政区`、`领导职务`。

5 个算子：

`机构全称`、`机构简称`、`上级机构`（主体：机构）；`政府`、`行政区划代码`（主体：行政区划）。

这些就是原来系统初始化的 CO v0.8 种子；现在不再标成“通用/无来源”，而是明确标为业务基线。

## 2. FIBO/CMNS 来源

- 源目录：`/home/oowpc/fibo-keol-import`（指向 `/home/oowpc/ontology-import`）
- 生成清单：`/home/oowpc/ontology-import/MANIFEST.json`
- FIBO commit：`2d73435a4419b66b7c9f44cb9763e35fe8ac7890`
- CMNS：19 个模块，158 个概念
- 组合结果：3150 个唯一概念（FIBO 4341 + CMNS 158 按冲突策略去重）
- 冲突策略：CMNS 优先；FIBO 同名带父类声明保留为次父类
- OMS 导入批次：`prod-20261027-1000`，来源 `/home/oowpc/fibo-keol-import`，actor `keol-sync:prod-20261027-1000`
- 导入后：3150 条概念带 `source_uri` / `ontology_module`；3385 个算子来自同一导入。

## 3. 已清理的测试数据

删除概念：

- `测试_流程验证_1020`（原 id 21），及其：
  - 1 条 `submissions`
  - 1 条 `tmp_mappings`
  - 1 条 `backfill_records`
  - 1 条 `concept_parents`
  - 0 个算子、0 条 usage

`audit_logs` 中保留历史 create/status 记录，并新增维护事件 `cleanup_test_data`，便于追溯；测试业务记录已删除。

清理脚本：`scripts/cleanup_baseline.py`（默认 dry-run，`--apply` 写库）。

## 4. 核验 SQL

```sql
-- 概念分层
SELECT domain, COUNT(*) FROM concepts GROUP BY domain ORDER BY COUNT(*) DESC;
-- 应看到 FIBO 2992、CMNS-* 158、业务基线 20

SELECT COUNT(*) FROM concepts;        -- 3170
SELECT COUNT(*) FROM operators;       -- 3390
SELECT COUNT(*) FROM ontology_rules;  -- 0
SELECT COUNT(*) FROM ontology_individuals; -- 0

-- 所有本体都有来源
SELECT COUNT(*) FROM concepts WHERE ontology_module IS NULL; -- 0
SELECT COUNT(*) FROM operators WHERE ontology_module IS NULL; -- 0

-- 无测试命名
SELECT id, name FROM concepts WHERE name LIKE '测试_%' OR name LIKE 'E2E%'; -- 0 行
```

## 5. 重新构建/导入

- 重新生成 FIBO+CMNS 规范导入包：`~/build_ontology.sh`（产出 `~/ontology-import`）。
- 导入前务必先做 SQLite 备份，并使用独立批次号；导入是名称自然键幂等 upsert。
- 新环境初始化后，如需要把 20 个 CO v0.8 种子标为业务基线，可执行：
  `python scripts/cleanup_baseline.py --apply`
  该脚本只精确清理已知测试概念并给 `DEMO_CONCEPTS` / `DEMO_OPERATORS` 打标签，不会动 FIBO/CMNS。

## 6. 边界

- **FIBO/CMNS 是参考本体**，不是组织自己的业务本体；它们和 CO v0.8 业务基线分域存放，便于后续替换/扩展。
- KEOL 加工台的 322 概念 / 229 算子 / 16 规则是**独立工作本体**，不属于本生产基线；需要时才通过 `/oms` 拉取/推送。
- 规则/个体为 0，后续进入时使用 `ontology_module` / `source_uri` 继续标注来源。
