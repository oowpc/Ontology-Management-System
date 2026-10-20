# 本体管理系统

覆盖概念本体「提交 → 查重 → 审核 → 入库 → 回填」全流程的管理系统（MVP 分模块交付）。

## 快速开始（本机）

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

打开 http://127.0.0.1:8000 ，种子账号：`admin` / `reviewer` / `annotator`（密码同用户名）；M7 演示组账号 `annotator2`（二组）/ `annotator3`（三组）。

- 数据库默认 `data/ontology.db`（SQLite）；切 PostgreSQL 设 `DB_URL`（见 `.env.example`）
- 首次启动自动建表 + 写入种子账号与演示概念（取自《中国政府本体 CO v0.8》）；`SEED_DEMO=0` 只建账号
- API 文档：http://127.0.0.1:8000/docs

## 模块进度

| 模块 | 内容 | 状态 |
| --- | --- | --- |
| M1 | 登录三角色 · 概念 CRUD（名称/别名/定义/父类/状态）· 算子 CRUD · 概念列表（过滤+使用统计）· 种子数据 | ✅ |
| M2 | 查重（编辑距离+别名命中+同父加权）· 高相似阻止直接入库 · 提交审核（理由/附题目/建议父类必填、批量通过驳回、合并）· TMP 发放 | ✅ |
| M3 | TMP 批量回填（resolved/merged → 正式概念名）+ 未决清单 + 回填记录可查 | ✅ 本次交付 |
| LLM | OpenAI 兼容接入（可降级）：提交页「语义复核」· 审核页「审核建议」（意见草稿+预选合并目标） | ✅ 本次交付 |
| M7 | KE 标注进度：各组每日全量上传标注 CSV（按题目ID覆盖 · 批次台账 · 未上传提醒）→ 五屏进度看板（全部组汇总 / 单组） | ✅ 本次交付 |
| M4 | 变更历史（写操作全字段 before/after 快照）· 字段级 diff · 一键回滚（回滚也留痕） | ✅ 本次交付 |
| M5 | 本体体检（未使用/孤立/近义对/算子计数）· 手动+APScheduler 定时 · 四 Tab 页 + CSV | ✅ 本次交付 |
| M6 | 导入导出（JSON/CSV、名称自然键、幂等 upsert、字段映射）+ Dockerfile / compose | ✅ 本次交付 |
| M8 | GOLDKE 管理（《数据标注执行方案》口径）：13 篇篇级流水线（CO对齐→大本体→KE对齐）· 20 天泳道排期 · issue 话题+评论+聚类 · 800/400 目标进度 · 验收抽查 | ✅ 本次交付 |
| M9 | GOLD 成品库：CSV 上传自动入草稿（篇编号+KE内容列）· 定稿锁定与版本 · 全留痕可回滚 · CSV/JSON 导出 · API 全覆盖 | ✅ 本次交付 |
| M10 | KE 逐条复核：待复核队列 · 逐条/批量打标 · 写回任务（一致→完成 / 不一致→退回+标签）· 系统结论上传重放 · 留痕可查 | ✅ 本次交付 |
| M11 | 通用化：CSV 列名映射（KE_COLUMN_MAP）· 状态词表映射（KE_STATUS_MAP）· GOLD 阶段/泳道词表配置 · 用户与组管理页（/admin/users） | ✅ 本次交付 |
| 接入 | Agent 全开放：Bearer Token（`/api/tokens` 自助签发）· 页面动作均有对应 API（32/32 审计通过）· 幂等键统一 · `AGENTS.md` 手册 | ✅ 本次交付 |

## M8 · GOLDKE 管理（执行方案口径）

- **篇任务**（`/gold/articles`）：方案预置 13 篇（育儿 10 + 特困 1 + 金融 1 + 化学 1）；阶段机 CO 标注与对齐 → 大本体整理 → KE 标注与对齐 → 已完成；每篇 KE 目标 30 条，完成数逐日更新
- **泳道排期**（`/gold/schedule`）：方案 20 天排期已预置（day × 上午/下午 × A/B/C 组），格子可编辑任务与状态（计划中/已完成/延误）
- **issue**（`/gold/issues`）：分歧文字登记（标题+思考过程+聚类标签+关联篇/题/领域），话题+评论推进，开放/已解决/已上会状态机
- **总览**（`/gold`）：累计 800 / 新增 400 目标进度条、按领域/组别/阶段统计、M7 题级完成数交叉核对
- **抽查**（`/gold/samples`）：验收期 sample 抽查登记（范围/抽查数/通过数/通过率）
- 目标口径配置：`GOLD_BASELINE`（存量 400）· `GOLD_TARGET_TOTAL`（800）· `GOLD_TARGET_NEW`（400）

## M9 · GOLD 成品库 + Agent 接入

- **成品入库**：每日 CSV 上传带可选「KE内容」列即自动入成品库草稿（篇编号+KE编号 幂等）；`/gold/items` 支持补录/编辑/过滤；`export.csv|json` 导出
- **定稿锁定**：草稿可由本组标注员修改；已复核 / GOLD 仅审核员、管理员可改，上传也不覆盖；写操作全部进「变更日志」可回滚
- **Agent 全开放**：详见根目录 `AGENTS.md`——`POST /api/tokens` 签发 Bearer Token 后，页面上的所有操作都可用 API 完成（上传、看板数据、成品 CRUD、定稿、导出、审核、回填、体检、导入导出）
- **通用性（别家工况）**：CSV 表头可用 `KE_COLUMN_MAP` JSON 单项覆盖映射；组名取自账号体系；种子演示数据用 `SEED_DEMO=0 / SEED_KE=0` 全部可关

## Docker 部署

```bash
docker compose up -d --build
# http://localhost:8000 ，数据在 named volume ontology-data（/data/ontology.db）
```

本机未装 Docker 时按「快速开始」用 uvicorn 跑即可，两者行为一致。

## LLM 配置（可选）

在环境变量中设置（OpenAI 兼容端点：DeepSeek / Qwen / 内网 vLLM / Ollama 均可）：

```bash
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-xxx
LLM_MODEL=deepseek-chat
```

**不配置时系统完全可用**：相关按钮自动隐藏，接口返回 `llm=false`，绝不阻断主流程。

- 提交页「LLM 语义复核」：编辑距离宽筛 + 同父兄弟 → LLM 判定 same（建议合并）/ near / distinct
- 审核页「LLM 建议」：按提交信息 + 查重快照 → 建议动作 + 意见草稿（自动填入）+ 预选合并目标

## M7 · KE 标注进度（每日上传 → 自动统计）

- **每天收工**：各组在 `/ke/upload` 上传本组标注表**全量导出 CSV**（十列 + 可选「篇编号」列（用于 GOLD 篇级完成数自动汇总）；重复上传按题目ID覆盖，以最新一次为准，发错重传即可）
- **自动统计**：`/ke/dashboard` 五屏看板（今日概览 / 进度趋势 / 结构与分布 / 人员与效率 / 问题面板），全部组汇总或按组切换，顶部横幅提示「今日未上传的组」；导出 PNG、生成周报文字按钮可直接用于上会
- **权限**：annotator 只能上传本组；reviewer/admin 可代传任意组；看板全员可见
- **配置**：`KE_DEADLINE`（截止日，理想线终点）、`SEED_KE=0` 关闭「一组」演示数据
- 指标口径（完成率/近7日均产能/预计完成日/返工率近似口径等）与独立版完全一致，见看板页脚「统计口径」

## 验证方法

```bash
pytest            # 79 项用例全绿（5 条验收标准均有对应用例）
```

| 验收标准 | 对应用例 |
| --- | --- |
| 1 近义给出相似列表并阻止直接入库 | `tests/test_submissions.py::test_similar_blocks_direct_create` |
| 2 TMP 批量替换正式 ID 且回填记录可查 | `tests/test_tmp.py::test_backfill_resolved_and_records` |
| 3 父类修改留 before/after 且可回滚 | `tests/test_audit.py::test_parent_change_history_and_rollback` |
| 4 体检列出无父类节点与未使用概念 | `tests/test_health.py::test_health_run_and_latest` |
| 5 导出 JSON 重新导入结构一致（幂等） | `tests/test_io.py::test_export_import_idempotent` |

页面手测：

1. **验收1**：`admin` 登录 → `/concepts/new` 直接建「政府」→ 报错并列出高相似（行政机关，别名命中），无法入库；`annotator` 在 `/submit` 提交「政府机关」→ 实时查重面板给出相似列表，照常进待审
2. **验收2**：`annotator` 提交拿到 `TMP-xxxx` → `reviewer` 通过 → `/tmp` 粘贴含 TMP 的 KE 文本回填 → 全部替换为正式概念名；映射表与回填记录（批次/处数/操作人）同页可查
3. **审核**：`/review` 待审单含提交时查重快照，填意见通过/驳回/合并，可勾选批量处理
4. **LLM**：配置后提交页出现「LLM 语义复核」、审核页出现「LLM 建议」

## 角色约定

| 角色 | 权限 |
| --- | --- |
| annotator 标注员 | 浏览概念/算子（M2 起可提交） |
| reviewer 审核员 | + 概念/算子增改、停用/恢复（M2 起审核） |
| admin 管理员 | 同 reviewer（M6 起 + 导入导出/回滚） |
