# OMS KE 语法校验

## 统一入口

所有 GOLD/KE 语法校验都走：

```text
app.ke.ke_validate.validate_ke()
```

调用方包括：

- GOLD 入库
- GOLD 编辑
- GOLD 定稿
- KE 上传
- 健康体检
- `/api/gold/items/{id}/validate`
- 批量重校

## 校验层

1. **Syntastic_and_Lexical-Verifier**
   - 路径：`KE_FORM_VALIDATOR_PATH`
   - 默认：`/home/oowpc/Syntastic_and_Lexical-Verifier`
   - 调用 `keval.form_validator.validate`
   - 结果保存在 `syntax` 字段，包含 `passed/complete/degraded/layer_status`

2. **OMS 内部语义检查**
   - 概念/算子闭词表
   - 算子 arity
   - KEOL 实例引用
   - `gold_items.concepts` 引用
   - 保留原 `errors/warnings` 结构

## 口径

OMS 统一使用 SQLke，不再兼容中文标识符、`var`、`--` 等旧方言。Syntastic 的语法硬错误
直接进入 `errors` 并阻断 GOLD 定稿。`unknown_concept` / `unknown_operator` / `unbound_var`
仍由 OMS 词表层作为 warning 处理，避免与本体词表重复。

## 降级

外部仓库或 `lark` 不可用时，`syntax_engine` 回退为 `internal`，仍保留原 OMS 校验。
