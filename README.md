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
| 接入 | Agent 全开放：Bearer Token（`/api/tokens` 自助签发）· 页面动作均有对应 API（32/32 审计通过）· 幂等键统一 · `AGENTS.md` 手册 | ✅ |
| M12 | Agent 可发现性：`GET /api/agent/manifest`（活路由表+工况词表+工作流配方，永不缺漏）· MCP server（`scripts/mcp_server.py`，47 个工具） | ✅ 本次交付 |
| M13 | 概念继承图：服务端 tidy-tree 布局 + 原生 SVG（零依赖、内网可用）· 缩放/拖拽/点击进详情 · 按根/状态过滤 · `/api/graph` 结构+布局数据 | ✅ |
| M14 | 领域分组：Concept.domain（默认通用，存量自动迁移）· 列表/图/导入导出/提交审核带领域 · 体检新增「跨领域近义对」（验收自洽口径） | ✅ 本次交付 |
| M15 | 粘贴识别：粘贴 KEOL 文本 → 自动识别 concept/op/const/KE 事实 → 报告（新/已存在/相似/无效+行级原因）→ 勾选导入（概念算子进本体库·可选领域，事实入成品库） | ✅ 本次交付 |
| M16 | 题目级视图：一题一页工作台（原文/CO/KE版本 diff/issue/审计尾）· `scripts/backfill_questions.py` 从 ke_tasks 幂等回填 · 概念详情反向入口 · 列表筛选+CSV | ✅ 本次交付 |
| M17 | Issue 管理闭环：分歧→讨论→结论→回填（7 表：issue+cases+待讨论+观点+评论+关联+影响清单）· IS 编号 · 结案强校验 · 重开留痕 · 概念/算子/题目页「相关 Issue」+ 预填提单 · MCP +5 工具 | ✅ 本次交付 |
| M18 | KE 看板 UI 迁移：Python 字符串模板（ke/template.py）→ Jinja 模板 `templates/ke/dashboard.html`，结构/样式/口径文字全部可直接编辑；metrics/service 数据逻辑零改动 | ✅ 本次交付 |
| M19 | 适配现行协作 xlsx：上传支持 .xlsx（QA 问答/Lean 题目标注 两种表头自动识别）· 多人 KE 列合成标注人 · Lean 的 conclusion 进复核结论、wpcke 进成品库 · 别家表头回退 KE_COLUMN_MAP | ✅ 本次交付 |
| M20 | KE 校验引擎：生产方言解析（全角标点/跨行 output/并列 const/比较约束宽待）· 结构 error（缺 output/形态错/已知签名 arity 不符）+ 词表 warning（未入库引用）· 定稿强校验（→gold 阻断，GOLD_FINALIZE_VALIDATE 可关）· 上传/编辑/入库即时校验 · 体检「成品校验」tab | ✅ 本次交付 |
| M22 | 导出指纹 manifest：`export.manifest.json` 逐条 sha256 + 链式 digest（对接下游数据版本守卫） | ✅ 本次交付 |
| M23 | LLM 加固：429/5xx/网络错误指数退避重试（LLM_MAX_RETRIES）· usage/耗时/成败落 llm_calls 台账 · `GET /api/llm/usage` 分场景汇总 | ✅ 本次交付 |
| M21 | 抽检闭环：分层等距抽样（seed 落库可复现）→ 双人盲评（他人结论盲化/重复 409）→ 分歧仲裁 → 关闭回填通过率 · Wilson 95% CI + 双评一致率 · 手工登记兼容 | ✅ 本次交付 |
| M24 | KEOL 词表桥接：`scripts/sync_keol.py` 把政府本体库 .md（```keol 块）的 concept/op 池幂等同步进本体库（名称自然键 upsert + 审计留痕）· 评审候选池（_ 开头文件）默认跳过、可选 draft 并入 | ✅ 本次交付 |
| M25 | KEOL 实例库镜像：`keol_entities`/`keol_facts` 两表（复用 KEOL 侧 load_db 解析口径，4.5 万实体/13.4 万事实秒级全量重建，`sync_keol.py --with-instances`）· 只读 API `/api/keol/status · entities · entities/{name} · facts` + `/keol` 查询页 · 概念详情页实例数 · M20 校验实体联查（`__引用`/常量右值命中镜像即放行，未收录记 unknown_entity；ENGINE v2）· MCP +4 | ✅ 本次交付 |
| M26 | GOLD→KEOL 回流：`scripts/gold_to_keol.py` 把定稿成品（默认 gold）的 concept/op/const 声明与坐实绑定翻成标准 KEOL，整文件重生成写入 KEOL 源树 `ke/回流/管理系统-GOLD.md`（其 load_db/keol_check 自然接管）· 已收录名字不重复声明、类型/父类未收录自动注释化（keol_check 保持全绿）· 变量坐实代入 `-- 问：` 注释 | ✅ 本次交付 |
| M27 | P0 安全加固：登录防爆破（连续失败 5 次锁 10 分钟，`LOGIN_MAX_FAILS`/`LOGIN_LOCK_SECONDS`）· CSRF 同步器 token（纯 ASGI 中间件缓冲体重放，base.html 自动注入隐藏域，Bearer 免校，`CSRF_ENABLED=0` 可关）· SECRET_KEY 兜底拒启动（非 `DEV_MODE` 下默认密钥直接 RuntimeError） | ✅ 本次交付 |

## M12 · Agent 可操控性补全

- **活清单**：`GET /api/agent/manifest`（Bearer 认证）——运行时内省全部 API 路由（新版 Starlette 懒加载路由已展开）、当前工况词表（GOLD 阶段/泳道/组别/目标、KE 状态与列名映射、查重阈值、LLM 是否可用）、4 条常用工作流配方、幂等键与错误约定。agent 首次接入调它即可自举，无需人工文档
- **MCP server**：`scripts/mcp_server.py`（官方 `mcp` SDK，stdio）。47 个工具覆盖：概念/算子/查重/提交审核/TMP 回填/GOLDKE 篇级与泳道/issue/KE 上传/成品校验与指纹/抽检闭环/体检/迁移/变更日志/LLM 台账

```jsonc
// .mcp.json 接入示例
{"mcpServers": {"ontology": {"command": "python", "args": ["scripts/mcp_server.py"],
  "env": {"ONTOLOGY_BASE_URL": "http://127.0.0.1:8000", "ONTOLOGY_TOKEN": "ke_..."}}}}
```

- 验证：`tests/test_agent.py`——Token 签发/吊销、manifest 结构、MCP 工具注册数、agent 经 MCP 完成「提交→审核→回填」全流程

## M21 · 抽检闭环（验收期口径，参考 NL2KE reviews_v2 + 分层抽样）

- **系统抽样**：按 组/篇/状态 圈定总体 → 按组别分层、层内等距抽取（最大余数法分配名额，seed 落 `params` 可复现）；样本清单落 `gold_sample_items` 可查
- **双人盲评**：每条样本需 2 名复核人（reviewer/admin，标注员回避）独立打标 通过/不通过；他人结论在「你已评或定局」前盲化；重复打标 409
- **定局**：两人一致自动落定；分歧进仲裁（第三人定论 + 仲裁人留痕）；全部定论方可关闭，关闭回填 total/passed（旧报表口径兼容）
- **统计**：通过率 + Wilson 95% CI、双评一致率、待仲裁数；手工登记（只记数字）保留为 kind=manual
- API：`POST /api/gold/samples/auto` · `GET /api/gold/samples/{id}` · `POST …/reviews` · `POST …/items/{gid}/arbitrate` · `POST …/close`（页面动作对称；MCP 同覆盖）

## M23 · LLM 加固

`chat()` 内部换为带重试的 `chat_with_usage`（口径参考 kelab/llm.py）：429/5xx/网络错误指数退避（1s/2s/4s…封顶 8s，次数 LLM_MAX_RETRIES 默认 2），4xx 不重试；每次调用（含失败）落 `llm_calls` 台账（场景/模型/tokens/耗时/重试次数/错误），`GET /api/llm/usage` 分场景汇总（reviewer/admin）。降级口径不变：彻底失败仍返回 None，主流程不受阻断。

## M20 · KE 校验引擎（参考 NL2KE ke_validate 口径，适配生产方言）

- **分级**：error（无法识别的行 / 缺 output 或多 output / 括号失衡 / concept·const·op 声明形态错 / 已知签名 arity 不符）**阻断定稿**；warning（概念/算子/常量未入库、变量未绑定、重复声明）不阻断，是「大本体整理」阶段的待办信号
- **方言宽待**（来自 244 条真实成品的适配）：全角标点自动归一（字符串内部不动）、output 跨行拼接、并列 `const A,B:类型`、`op 名(主体)=类型` 写法、绑定段比较约束（`>=`/`<:`/裸谓词调用）、行首项目符号
- **挂接**：入库/编辑/上传即时校验（结果缓存 `gold_items.validation`，引擎版本变更批量重算）→ 列表「校验」列徽标 → 编辑页问题明细 → 体检「成品校验」tab（可下 CSV）→ 定稿门槛（422 + 首条错误提示）
- **实测**：首批 244 条真实成品 90 条有结构错误（讨论笔记混入成品库、缺 output、签名冲突），全部在之前不可见
- API：`POST /api/gold/items/{id}/validate`（单条明细）· `POST /api/gold/items/validate-all`（批量，幂等只补陈旧缓存）

## M22 · 导出指纹 manifest（数据版本守卫）

`GET /api/gold/items/export.manifest.json`（过滤参数同列表）：逐条 `sha256(ke_text.strip())` + 按（篇，编号）排序的链式 `digest` + 状态分布。下游实验平台可据此校验手头数据与库中是否同一版本（对应 NL2KE 冻结 manifest 口径）。

## M19 · 适配现行协作 xlsx（上传即入）

- **上传口同时吃 CSV 和 .xlsx**：`/ke/upload` 与 `POST /api/ke/upload` 按扩展名分发；xlsx 用 openpyxl（`reset_dimensions` 兼容协作系统/WPS 导出的错误 dimension）
- **两种现行格式自动识别**：
  - QA 问答（自然语言/Gold_KE/KE-*/KE-对齐状态/Issue）：题号取 `[山东问N]` 前缀；KE 内容 = `Gold_KE`；对齐状态进复核结论；`Issue` 非空打 issue 标签
  - Lean 题目标注（题目/题干/领域/conclusion/wpcke/各人 KE 列/所属数据集）：KE 内容 = `wpcke`（空则回退各人 KE 列）；`conclusion` 是讨论文本 → 进复核结论；标注人 = 有内容的多人列合成
- **篇编号**：行内「所属数据集」→ 工作表名 → 文件名去扩展名
- **别家表头**：不认识时回退 `KE_COLUMN_MAP` 规范列匹配（与 CSV 同口径）
- 实测：`育儿补贴-问答.xlsx`（75 行，69 完成）、`Lean题目标注.xlsx`（51 行，50 完成）均零警告入库，成品库分别命中 Gold_KE 与 wpcke

## M17 · Issue 管理（分歧闭环）
- **闭环**：编号 IS-xxxx → 问题描述（例子→矛盾→提问）+ 对照案例（原文/写法一/写法二/差异）→ 待讨论问题与各方观点 → 结案（结论+适用范围+影响清单强校验）→ 回填清单勾选完成（进度 x/y）→ 重开留痕（结案不锁定）
- **校验**：标题以「关于/讨论/记录」开头被拒（引导写成问句）；每条 Case 需原文+至少一种写法；新建至少 1 条 Case；结案影响清单为空须显式声明「无受影响条目」
- **集成**：概念详情 / 算子详情（本模块新增）/ 题目详情各有「相关 Issue」块 + 「提 issue」预填链接；MCP 增加 issue_create / issue_comment / issue_close / issue_list_impacts / issue_reopen / list_issues
- **边界**：gold/issues 与题目便签是模块内轻量记录（未改动）；本模块是跨对象正式分歧体系
- 全部写操作 `entity_type='issue'` 留痕；页面动作与 `/api/issues/*` 对称

## M16 · 题目级视图（一题一页工作台）
- **回填**：`python scripts/backfill_questions.py` 从 ke_tasks 生成题目（`--no-link` 关 CO 自动关联）；ext_id 幂等（跨组重号消歧为 `组/题号`），重复运行零新建；页面/API 也可点「从 KE 上传同步」
- **详情五块**：题目头（状态流转，过审/退回仅 reviewer+）→ 原文+命中概念 → CO 面板（概念带「被多少题使用」、算子带签名；可添加关联，二选一约束）→ KE 面板（当前版等宽保留换行、任意两版 difflib 逐行 diff、另存新版本仅 annotator/admin）→ issue 面板（未解决在前、停留>3 天高亮）→ 审计尾（最近 10 条变动）
- **列表**：领域/组/状态/未解决 issue/关键字筛选，默认「未解决 issue 在前 + 更新时间倒序」，50/页，当前筛选可导 CSV
- **反向入口**：概念详情页「使用此概念的题目」折叠块（前 20 条）
- 全部写操作 `entity_type='question'` 留痕；页面动作与 `/api/questions/*` 对称（manifest 自动列出）

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
