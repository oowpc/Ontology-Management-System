# 规则/个体跨 OMS 实例迁移

规则 `body/head` 保存的是 **OMS 实例 ID**（`oms-c-*` / `oms-op-*`），不能直接把规则 JSON 搬到另一个 OMS 实例。

## 1. 导出带 ID 的快照

```bash
curl -H "Authorization: Bearer <admin-token>" \
  https://oms.example/api/export/portable > old-portable.json
```

与普通 `/api/export/json` 不同，portable 导出给 concepts/operators 增加 `id`，供规则引用重映射使用。

## 2. remap

在目标 OMS 同样导出 `target-portable.json`，然后：

```bash
python scripts/remap_rule_refs.py \
  --source old-portable.json \
  --target target-portable.json \
  --out rules-remapped.json
```

映射键：

- 概念：名称（概念名全局唯一）
- 算子：`(name, subject)`（OMS 算子身份键）
- 常量 `belong_concepts` 与规则 `operator` 引用都会重写
- 找不到目标引用时写 `warnings`，不静默编造

输出 `rules-remapped.json` 可直接导入目标 OMS：

```bash
curl -X POST -H "Authorization: Bearer <target-admin-token>" \
  -H 'Content-Type: application/json' \
  --data-binary @rules-remapped.json \
  https://target-oms/api/import/json
```

## 3. 边界

- 目标 OMS 必须先有同名概念和同 `(name, subject)` 算子。
- 内建/外部引用（不是 `oms-c-*` / `oms-op-*`）保持原样。
- 规则同名会按目标 OMS 的导入规则 upsert；建议先用建议箱或导出备份。
- 该工具只 remap 规则/个体，不负责概念/算子的语义合并。
