# OMS 统一校验

## 入口

所有 rule / operator / GOLD KE 的写入都经过 `app.validation.run()`：

```python
from app.validation import run

result = run(db, "rule", rule, stage="create", actor=username)
```

- `rule`：结构、状态、重名、概念/算子引用、算子 arity。
- `operator`：主体/owner、重名、参数/返回类型闭包。
- `gold_item`：现有 `validate_ke()`（Syntastic 语法 + OMS 词表/语义）。
- 每次结果追加写入 `validation_results`，不覆盖历史。

## 阻断策略

`VALIDATION_ENFORCE=0`（默认）：只记录结果，不阻断写入，适合历史数据清理期。

`VALIDATION_ENFORCE=1`：存在 `errors` 时阻断写入；`warnings` 不阻断。
草稿对象和 GOLD 草稿入库始终允许保存，但会留下校验结果；GOLD 定稿仍受
`GOLD_FINALIZE_VALIDATE` 控制。

## 运营页

`/validation` 提供当前库的校验历史、类型统计、错误/警告筛选和批量重校；
批量重校最多 500 条，只记录结果，不自动改变对象状态。

## API

- `GET /api/validation/results`：分页查询结果
- `GET /api/validation/results/{id}`：查看完整 issues
- `GET /api/validation/summary`：按对象类型统计
- `POST /api/validation/run`：单对象重校
- `POST /api/validation/revalidate`：批量重校，`{"object_type": "rule", "limit": 100}`

## 回填

```bash
.venv/bin/python scripts/backfill_validation.py --limit 1000
```

回填只记录，不改变对象状态，不阻断。

## 语义边界

统一的是入口、结果结构、留痕和阻断策略，不是让 rule/operator/KE 共用同一个
解析器。规则目前不做定理证明或完整环推理；算子不做函数体/精确类型推导。
