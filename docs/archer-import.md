# Archer 数据集导入

## 来源

- 本体：`/home/oowpc/NL2KE/Archer/super_ontology.json`
- 表结构：`/home/oowpc/NL2KE/Archer/tables.json`
- 示例：`/home/oowpc/NL2KE/Archer/train_ke_sql_enall.json`（414 条 KE/SQL，暂未导入）

导入脚本：`scripts/import_archer.py`

## 当前结果

已导入新库 `archer`（reference）：

| 项 | 数量 |
|---|---:|
| 概念 | 639 |
| 算子 | 4622 |
| PROPERTY | 3696 |
| LINK | 926 |
| 数据库 | 206 |
| 表 | 1053 |
| 导入批次 | `archer-20261031-035639` |

所有 `PROPERTY` 的返回类型已从 `tables.json` 推断并映射为：

- `text -> String`
- `integer/real -> Number`
- `date/datetime -> Date`
- `boolean -> Bool`

所有 `LINK` 的源/目标概念都能在 Archer 概念集中解析。

## 映射

- `concept` → OMS 概念；`domain=Archer`，`source_uri=archer://db/table`
- `PROPERTY` → `op Name(Concept)：String/Number/Date/Bool`
- `LINK` → `op Name(Source)：Target`
- `description` 保留 Archer note、字段、数据库、LINK path/bindings 文本
- `ontology_module` 为 `Archer:PROPERTY` / `Archer:LINK`

## 概念树

已按来源数据库建立层级（`scripts/build_archer_tree.py`）：

```text
Archer（根，1 个）
  ├── aan_1（数据库分组，199 个）
  │     └── Author / Paper / ...
  ├── baseball_1
  └── ...
```

- 原始概念：639
- 数据库分组概念：199
- 根概念：1
- Archer 库概念合计：839
- 编号：`ARCH-0001` 起；自动生成的库内编号，全局 `id` 仍是物理主键
- 描述：639 个原始概念按来源数据库、表和字段生成 `description`；算子保留 Archer note
- 概念父类链接：1181（982 条来源关系 + 199 条根分组）
- 多来源概念保留多父类，例如 `Author` 同时挂在 `aan_1 / book_1 / book_press / ...` 下
- 数据库分组 `domain=Archer:Database`；原始概念 `domain=Archer`

查看：切到 `archer` 后打开 `/graph?root_id=3811`，或在概念列表按父类筛选 `Archer`。

## 已知限制

- 数据库分组概念是导航节点，不是 Archer 原始实体。
- Archer 源文件没有 is-a 语义层级；这里只做来源分组，不伪造语义父子关系。
- LINK 的 `path/bindings` 只保存在 `description`，尚未结构化存储。
- 414 条 `train_ke_sql_enall` 示例、104 道 NL2SQL dev 题没有作为本体导入。
- 206 个数据库的完整 SQLite 数据不在本机；本机只有 NL2SQL dev 的 2 个库。

## 访问

`archer` 是 reference 库。初始授权：

- `reviewer`：owner
- `annotator / annotator2 / annotator3`：annotator
- `admin`：全局通过

后续在 `/libraries` 管理成员。

## 回滚

导入前备份：`data/backups/ontology-20261031-035635.db`  
导入后备份：`data/backups/ontology-20261031-035722.db`  
建树前备份：`data/backups/ontology-20261031-041300.db`  
建树后备份：`data/backups/ontology-20261031-041318.db`

如需回滚整个 Archer 库，使用数据库备份；不要用单条概念回滚代替整库回滚。
