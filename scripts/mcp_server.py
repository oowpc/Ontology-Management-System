"""MCP stdio server：把本体管理系统 API 暴露为 MCP 工具，agent 直连操控（83 个工具）。

启动（两种认证）：
  ONTOLOGY_TOKEN=ke_... python scripts/mcp_server.py
  或先拿 session 自助签发：见 AGENTS.md §1

环境变量：
  ONTOLOGY_BASE_URL  默认 http://127.0.0.1:8000
  ONTOLOGY_TOKEN     Bearer Token（POST /api/tokens 签发）
  ONTOLOGY_LIBRARY   本体库 key（默认 co-baseline；写操作务必显式设置）

Claude Code 接入示例（.mcp.json）：
  {"mcpServers": {"ontology": {"command": "python", "args": ["scripts/mcp_server.py"],
    "env": {"ONTOLOGY_TOKEN": "ke_...", "ONTOLOGY_LIBRARY": "co-baseline"}}}}
"""
from __future__ import annotations

import os

import httpx

try:  # mcp < 2
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError:  # mcp 2.x: FastMCP renamed to MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP

BASE_URL = os.environ.get("ONTOLOGY_BASE_URL", "http://127.0.0.1:8000")
TOKEN = os.environ.get("ONTOLOGY_TOKEN", "")
LIBRARY = os.environ.get("ONTOLOGY_LIBRARY", "co-baseline")

mcp = FastMCP("ontology-manager")

_client = None


def build_client() -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {TOKEN}",
                 "X-Ontology-Library": LIBRARY},
        timeout=30,
    )


def api(method: str, path: str, **kw):
    """统一调用与错误包装：返回 dict（出错时 {error, detail}）。"""
    global _client
    if _client is None:
        _client = build_client()
    r = _client.request(method, path, **kw)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        return {"error": r.status_code, "detail": detail}
    if not r.content:
        return {"ok": True}
    return r.json()


# ---------------- 自检与发现 ----------------

@mcp.tool()
def whoami() -> dict:
    """当前 Token 对应的用户与角色（接入自检）。"""
    return api("GET", "/api/me")


@mcp.tool()
def get_manifest() -> dict:
    """系统活清单：全部 API 路由、当前工况词表、常用工作流、不变量。首次接入先调它。"""
    return api("GET", "/api/agent/manifest")


# ---------------- 本体：概念/算子/查重 ----------------

@mcp.tool()
def list_concepts(q: str = "", status: str = "", parent_id: int | None = None,
                  page: int = 1, size: int = 50) -> dict:
    """概念列表（名称/别名/定义关键字，状态 draft|pending|approved|disabled）。"""
    params: dict = {"page": page, "size": size}
    if q:
        params["q"] = q
    if status:
        params["status"] = status
    if parent_id is not None:
        params["parent_id"] = parent_id
    return api("GET", "/api/concepts", params=params)


@mcp.tool()
def get_concept(concept_id: int) -> dict:
    """概念详情（含子类与绑定算子）。"""
    return api("GET", f"/api/concepts/{concept_id}")


@mcp.tool()
def create_concept(name: str, aliases: list[str] | None = None, definition: str = "",
                   parent_id: int | None = None) -> dict:
    """直接建概念（reviewer/admin）；高相似会被 409 拦截并返回相似列表——多数情况应走 submit_concept。"""
    return api("POST", "/api/concepts", json={
        "name": name, "aliases": aliases or [], "definition": definition, "parent_id": parent_id,
    })


@mcp.tool()
def update_concept(concept_id: int, name: str | None = None, aliases: list[str] | None = None,
                   definition: str | None = None, parent_id: int | None = None) -> dict:
    """改概念字段（留 before/after 快照）；parent_id 传 null 表示清空父类。"""
    body = {k: v for k, v in {"name": name, "aliases": aliases,
                              "definition": definition, "parent_id": parent_id}.items()
            if v is not None}
    return api("PATCH", f"/api/concepts/{concept_id}", json=body)


@mcp.tool()
def check_similarity(name: str, aliases: list[str] | None = None,
                     parent_id: int | None = None) -> dict:
    """查重：返回相似概念（名称/别名/编辑距离/父类/分数）与拦截阈值。"""
    params: list = [("name", name)]
    for a in aliases or []:
        params.append(("aliases", a))
    if parent_id is not None:
        params.append(("parent_id", parent_id))
    return api("GET", "/api/similarity/check", params=params)


@mcp.tool()
def list_operators(subject_concept_id: int | None = None, q: str = "") -> dict:
    """算子列表（可按主体概念过滤）。"""
    params: dict = {}
    if subject_concept_id is not None:
        params["subject_concept_id"] = subject_concept_id
    if q:
        params["q"] = q
    return api("GET", "/api/operators", params=params)


# ---------------- 提交审核与 TMP 回填 ----------------

@mcp.tool()
def submit_concept(name: str, suggested_parent_id: int, reason: str, sample_question: str,
                   aliases: list[str] | None = None, definition: str = "") -> dict:
    """标注员提交新概念（三项必填；发 TMP-xxxx；带查重快照进待审）。"""
    return api("POST", "/api/submissions", json={
        "name": name, "aliases": aliases or [], "definition": definition,
        "suggested_parent_id": suggested_parent_id,
        "reason": reason, "sample_question": sample_question,
    })


@mcp.tool()
def list_submissions(status: str = "pending") -> dict:
    """提交单列表（pending/approved/rejected/merged）。"""
    return api("GET", "/api/submissions", params={"status": status})


@mcp.tool()
def review_submission(submission_id: int, action: str, comment: str,
                      merge_target_id: int | None = None) -> dict:
    """审核提交单（reviewer/admin）：action=approve|reject|merge；意见必填；merge 需 merge_target_id。"""
    return api("POST", f"/api/submissions/{submission_id}/{action}",
               json={"comment": comment, "merge_target_id": merge_target_id})


@mcp.tool()
def list_tmp_mappings(status: str = "") -> dict:
    """TMP 映射表（open/resolved/merged）。"""
    params = {"status": status} if status else {}
    return api("GET", "/api/tmp-mappings", params=params)


@mcp.tool()
def backfill_text(text: str, source_label: str = "") -> dict:
    """批量回填：文本中的 TMP-xxxx 替换为正式概念名；返回替换后文本与未决清单。"""
    return api("POST", "/api/tmp/backfill", json={"text": text, "source_label": source_label})


# ---------------- GOLDKE ----------------

@mcp.tool()
def gold_stats() -> dict:
    """GOLDKE 总览：800/400 目标进度、按领域/组别/阶段汇总、M7 交叉核对。"""
    return api("GET", "/api/gold/stats")


@mcp.tool()
def list_gold_articles(domain: str = "", group_name: str = "", stage: str = "") -> dict:
    """篇任务列表（阶段：co_annotation→co_merge→ke_annotation→done）。"""
    params = {k: v for k, v in {"domain": domain, "group_name": group_name,
                                "stage": stage}.items() if v}
    return api("GET", "/api/gold/articles", params=params)


@mcp.tool()
def advance_gold_article(article_id: int) -> dict:
    """篇任务推进到下一阶段。"""
    return api("POST", f"/api/gold/articles/{article_id}/advance")


@mcp.tool()
def update_gold_ke_done(article_id: int, ke_done: int) -> dict:
    """更新篇任务已完成 KE 条数。"""
    return api("PATCH", f"/api/gold/articles/{article_id}", json={"ke_done": ke_done})


@mcp.tool()
def upsert_schedule_cell(day_index: int, slot: str, group_name: str, task: str,
                         article_code: str = "", status: str = "planned",
                         note: str = "") -> dict:
    """泳道格子写入（day×时段×组别；status=planned|done|missed）。"""
    return api("POST", "/api/gold/schedule/cell", json={
        "day_index": day_index, "slot": slot, "group_name": group_name,
        "task": task, "article_code": article_code, "status": status, "note": note,
    })


@mcp.tool()
def list_gold_issues(status: str = "", cluster: str = "") -> dict:
    """标注分歧 issue 列表（open/resolved/escalated；可按聚类过滤）。"""
    params = {k: v for k, v in {"status": status, "cluster": cluster}.items() if v}
    return api("GET", "/api/gold/issues", params=params)


@mcp.tool()
def create_issue(title: str, content: str = "", cluster: str = "",
                 related_type: str = "", related_ref: str = "") -> dict:
    """登记分歧 issue（话题+评论形式；content 写思考过程）。"""
    return api("POST", "/api/gold/issues", json={
        "title": title, "content": content, "cluster": cluster,
        "related_type": related_type, "related_ref": related_ref,
    })


@mcp.tool()
def comment_issue(issue_id: int, content: str) -> dict:
    """给 issue 追加评论。"""
    return api("POST", f"/api/gold/issues/{issue_id}/comments", json={"content": content})


@mcp.tool()
def set_issue_status(issue_id: int, status: str, resolution: str = "") -> dict:
    """issue 状态流转：open|resolved|escalated（解决/上会时填 resolution）。"""
    return api("POST", f"/api/gold/issues/{issue_id}/status",
               json={"status": status, "resolution": resolution})


# ---------------- KE 进度、体检、迁移 ----------------

@mcp.tool()
def list_ke_groups() -> dict:
    """各组今日上传状态 / 最近批次。"""
    return api("GET", "/api/ke/groups")


@mcp.tool()
def upload_ke_csv(csv_text: str, filename: str = "agent-upload.csv") -> dict:
    """KE 标注表全量上传（CSV 文本；幂等键=组+题目ID；表头可用 KE_COLUMN_MAP 配）。"""
    return api("POST", "/api/ke/upload",
               files={"file": (filename, csv_text.encode("utf-8"), "text/csv")})


@mcp.tool()
def run_health() -> dict:
    """手动触发本体体检（未使用/孤立/近义对/算子计数；reviewer/admin）。"""
    return api("POST", "/api/health/run")


@mcp.tool()
def get_health_latest() -> dict:
    """最近一次体检报告（含明细）。"""
    return api("GET", "/api/health/latest")


@mcp.tool()
def export_ontology() -> dict:
    """导出全量本体（JSON；名称自然键可移植；admin）。"""
    return api("GET", "/api/export/json")


@mcp.tool()
def list_history(entity_type: str = "", limit: int = 50) -> dict:
    """变更日志（全部写操作的 before/after 快照）。"""
    params: dict = {"limit": limit}
    if entity_type:
        params["entity_type"] = entity_type
    return api("GET", "/api/history", params=params)


# ---------------- Issue 闭环 ----------------

@mcp.tool()
def list_issues(status: str = "", domain: str = "", type_tag: str = "", q: str = "") -> dict:
    """Issue 列表（状态/领域/类型/关键字筛选；未结案在前）。"""
    params = {k: v for k, v in {"status": status, "domain": domain,
                                "type_tag": type_tag, "q": q}.items() if v}
    return api("GET", "/api/issues", params=params)


@mcp.tool()
def issue_create(title: str, type_tag: str, description: str, cases: list[dict],
                 domain: str = "通用", group_name: str = "", importance: str = "",
                 urgency: str = "", entity_link: dict | None = None) -> dict:
    """创建 issue。cases 至少 1 条：{original_text 必填, variant_a/b 至少一个, source?, diff_note?}。
    entity_link 可带关联：{"entity_type": "concept|operator|question|ke_version|gold_item", "entity_id": 1}"""
    return api("POST", "/api/issues", json={
        "title": title, "type_tag": type_tag, "description": description,
        "domain": domain, "group_name": group_name,
        "importance": importance or None, "urgency": urgency or None,
        "cases": cases, "entity_link": entity_link,
    })


@mcp.tool()
def issue_comment(issue_id: int, body: str) -> dict:
    """给 issue 追加评论（讨论流水）。"""
    return api("POST", f"/api/issues/{issue_id}/comments", json={"body": body})


@mcp.tool()
def issue_close(issue_id: int, conclusion: str, scope: str, open_items: str = "",
                impacts: list[dict] | None = None, impact_none: bool = False) -> dict:
    """结案：结论+适用范围必填；impacts=[{entity_type, entity_id, action?}]；
    无受影响条目必须 impact_none=True。结案后生成回填清单。"""
    return api("POST", f"/api/issues/{issue_id}/close", json={
        "conclusion": conclusion, "scope": scope, "open_items": open_items,
        "impacts": impacts or [], "impact_none": impact_none,
    })


@mcp.tool()
def issue_list_impacts(issue_id: int) -> dict:
    """某 issue 的受影响条目与完成进度（回填清单）。"""
    return api("GET", f"/api/issues/{issue_id}/impacts")


@mcp.tool()
def issue_reopen(issue_id: int) -> dict:
    """重新打开已结案 issue（状态回 discussing，留痕）。"""
    return api("POST", f"/api/issues/{issue_id}/reopen")


# ---------------- M20-M23 · 成品校验 / 指纹 / 抽检闭环 / LLM 台账 ----------------

@mcp.tool()
def gold_validate_item(item_id: int) -> dict:
    """重校单条成品 KE（M20）：返回 errors（阻断定稿）/warnings（不阻断）明细并刷新缓存。"""
    return api("POST", f"/api/gold/items/{item_id}/validate")


@mcp.tool()
def gold_validate_all() -> dict:
    """批量重校全部成品（引擎版本旧的才重算；reviewer/admin）。"""
    return api("POST", "/api/gold/items/validate-all")


@mcp.tool()
def gold_export_manifest(article_code: str = "", status: str = "", group_name: str = "") -> dict:
    """M22：导出指纹 manifest（逐条 sha256 + 链式 digest，数据版本守卫）。"""
    params = {k: v for k, v in {"article_code": article_code, "status": status,
                                "group_name": group_name}.items() if v}
    return api("GET", "/api/gold/items/export.manifest.json", params=params)


@mcp.tool()
def gold_sample_auto_create(group_name: str = "", article_code: str = "", status: str = "",
                            size: int = 0, rate: float = 0.1, seed: int | None = None,
                            note: str = "") -> dict:
    """M21：系统抽样创建抽查（分层等距，seed 可复现；size 与 rate 二选一，size 优先）。"""
    return api("POST", "/api/gold/samples/auto", json={
        "group_name": group_name, "article_code": article_code, "status": status,
        "size": size, "rate": rate, "seed": seed, "note": note})


@mcp.tool()
def gold_sample_detail(sample_id: int) -> dict:
    """抽查详情：样本清单 + 统计（通过率含 Wilson 95% CI、双评一致率、待仲裁数）。

    盲评口径：他人结论对未评者盲化（你已评或该条目定局后可见）。"""
    return api("GET", f"/api/gold/samples/{sample_id}")


@mcp.tool()
def gold_sample_review(sample_id: int, gold_item_id: int, verdict: str, note: str = "") -> dict:
    """抽检打标：verdict=通过|不通过；每人每条一次（重复 409）；需双人，分歧待仲裁。"""
    return api("POST", f"/api/gold/samples/{sample_id}/reviews",
               json={"gold_item_id": gold_item_id, "verdict": verdict, "note": note})


@mcp.tool()
def gold_sample_arbitrate(sample_id: int, gold_item_id: int, verdict: str, note: str = "") -> dict:
    """仲裁分歧条目（须已有两人且结论不一致）。"""
    return api("POST", f"/api/gold/samples/{sample_id}/items/{gold_item_id}/arbitrate",
               json={"verdict": verdict, "note": note})


@mcp.tool()
def gold_sample_close(sample_id: int) -> dict:
    """关闭抽查（需全部样本有最终结论）；关闭时回填 total/passed。"""
    return api("POST", f"/api/gold/samples/{sample_id}/close")


@mcp.tool()
def llm_usage() -> dict:
    """M23：LLM 调用台账汇总（分场景次数/tokens/均耗时/失败数；reviewer/admin）。"""
    return api("GET", "/api/llm/usage")


# ---------------- M25 · KEOL 实例库（只读镜像） ----------------

@mcp.tool()
def keol_status() -> dict:
    """KEOL 镜像概况：实体/事实/类型/谓词数 + 最近同步时间（sync_keol.py --with-instances 灌入）。"""
    return api("GET", "/api/keol/status")


@mcp.tool()
def keol_list_entities(q: str = "", type: str = "", limit: int = 50, offset: int = 0) -> dict:
    """KEOL 实体检索：名称模糊 q / 类型精确 type（如 地级行政区）。"""
    return api("GET", "/api/keol/entities", params={"q": q, "type": type, "limit": limit, "offset": offset})


@mcp.tool()
def keol_get_entity(name: str) -> dict:
    """KEOL 实体详情：字面量属性 / 出向事实 / 入向事实。name 形如 __广东省。"""
    return api("GET", f"/api/keol/entities/{name}")


@mcp.tool()
def keol_list_facts(pred: str = "", subj: str = "", value: str = "", limit: int = 200) -> dict:
    """KEOL 事实检索：谓词/主体/值精确过滤（可组合），如 pred=上级行政区划 value=__广州市。"""
    return api("GET", "/api/keol/facts", params={"pred": pred, "subj": subj, "value": value, "limit": limit})


if __name__ == "__main__":
    mcp.run()  # stdio


# ---------------- Agent JSON API: 标注工作流 ----------------

@mcp.tool()
def list_annotation_batches(q: str = "", state: str = "", page: int = 1, size: int = 50) -> dict:
    """列出当前用户可见的标注批次（admin 全部；其他用户仅自己参与的批次）。"""
    return api("GET", "/api/annotation/batches",
               params={"q": q, "state": state, "page": page, "size": size})


@mcp.tool()
def get_annotation_batch(batch_id: int) -> dict:
    """批次详情：状态、成员、统计、版本、权限标记。"""
    return api("GET", f"/api/annotation/batches/{batch_id}")


@mcp.tool()
def create_annotation_batch(name: str, source_name: str, guide_version: str,
                            source_checksum: str, annotator_a: str = "",
                            annotator_b: str = "", lead: str = "",
                            cross_checkers: list[str] | None = None,
                            project_owner: str = "", arbiter: str = "",
                            batch_id: str = "", source_version: str = "",
                            source_type: str = "policy", domain: str = "通用",
                            statistical_unit: str = "标注单元",
                            acceptance_threshold: str = "", note: str = "") -> dict:
    """创建标注批次（reviewer/admin）；cross_checkers 传用户名数组。"""
    return api("POST", "/api/annotation/batches", json={
        "batch_id": batch_id or None, "name": name, "source_name": source_name,
        "source_version": source_version, "source_checksum": source_checksum,
        "source_type": source_type, "domain": domain, "guide_version": guide_version,
        "statistical_unit": statistical_unit,
        "acceptance_threshold": acceptance_threshold, "annotator_a": annotator_a,
        "annotator_b": annotator_b, "lead": lead,
        "cross_checkers": cross_checkers or [], "project_owner": project_owner,
        "arbiter": arbiter, "note": note,
    })


@mcp.tool()
def add_annotation_units(batch_id: int, units: list[dict]) -> dict:
    """向草稿批次导入/更新标注单元；units 每项至少有 unit_id。"""
    return api("POST", f"/api/annotation/batches/{batch_id}/units", json={"units": units})


@mcp.tool()
def list_annotation_units(batch_id: int, page: int = 1, size: int = 100) -> dict:
    """列出批次单元及 CO/对齐/KE/GOLD 完成标记。"""
    return api("GET", f"/api/annotation/batches/{batch_id}/units",
               params={"page": page, "size": size})


@mcp.tool()
def get_annotation_unit(unit_id: int) -> dict:
    """单元详情：当前 CO/对齐/KE/GOLD、evidence、执行记录、当前用户可执行动作。"""
    return api("GET", f"/api/annotation/units/{unit_id}")


@mcp.tool()
def add_annotation_evidence(unit_id: int, quote: str, source_id: str = "",
                            locator: dict | None = None, language: str = "zh",
                            kind: str = "text") -> dict:
    """给标注单元添加 evidence。"""
    return api("POST", f"/api/annotation/units/{unit_id}/evidence", json={
        "source_id": source_id, "locator": locator, "quote": quote,
        "language": language, "kind": kind,
    })


@mcp.tool()
def submit_annotation_co(unit_id: int, side: str,
                         concepts: list[str] | None = None,
                         constants: list[str] | None = None,
                         operators: list[str] | None = None,
                         decision: str = "include", reason: str = "",
                         evidence_ids: list[int] | None = None) -> dict:
    """提交 A/B 侧 CO（双盲阶段；只能提交自己被分配的侧）。"""
    return api("POST", f"/api/annotation/units/{unit_id}/co", json={
        "side": side, "decision": decision, "concepts": concepts or [],
        "constants": constants or [], "operators": operators or [],
        "reason": reason, "evidence_ids": evidence_ids or [],
    })


@mcp.tool()
def submit_annotation_alignment(unit_id: int, conclusion: str = "aligned",
                                concepts: list[str] | None = None,
                                constants: list[str] | None = None,
                                operators: list[str] | None = None,
                                concept_rows: list[dict] | None = None,
                                operator_rows: list[dict] | None = None,
                                differences: list[str] | None = None,
                                reason: str = "", evidence_ids: list[int] | None = None) -> dict:
    """提交逐题 CO 对齐结论；concept_rows/operator_rows 可直接驱动 CO_M 应用。"""
    return api("POST", f"/api/annotation/units/{unit_id}/align", json={
        "conclusion": conclusion, "concepts": concepts or [],
        "constants": constants or [], "operators": operators or [],
        "concept_rows": concept_rows, "operator_rows": operator_rows,
        "differences": differences or [], "reason": reason,
        "evidence_ids": evidence_ids or [],
    })


@mcp.tool()
def submit_annotation_ke(unit_id: int, side: str, content: str,
                         evidence_ids: list[int] | None = None,
                         execution: dict | None = None,
                         self_check: dict | None = None) -> dict:
    """提交 A/B 侧 KE；Query 数据集建议同时传 execution。"""
    return api("POST", f"/api/annotation/units/{unit_id}/ke", json={
        "side": side, "content": content, "evidence_ids": evidence_ids or [],
        "execution": execution, "self_check": self_check,
    })


@mcp.tool()
def submit_annotation_gold(unit_id: int, content: str, source: str = "A",
                           differences: list[str] | None = None, reason: str = "",
                           evidence_ids: list[int] | None = None) -> dict:
    """提交逐题 GOLD KE。"""
    return api("POST", f"/api/annotation/units/{unit_id}/gold", json={
        "source": source, "content": content, "differences": differences or [],
        "reason": reason, "evidence_ids": evidence_ids or [],
    })


@mcp.tool()
def run_annotation_query(unit_id: int, side: str) -> dict:
    """对 SQL/SPARQL/LEAN 单元执行/编译并留痕。"""
    return api("POST", f"/api/annotation/units/{unit_id}/run-query", json={"side": side})


@mcp.tool()
def advance_annotation_batch(batch_id: int, action: str,
                             return_state: str = "") -> dict:
    """状态推进：freeze/start_co/start_alignment/start_merge/start_ke/start_gold/start_cross/validate/pass/rework/resume。"""
    body = {"action": action}
    if return_state:
        body["return_state"] = return_state
    return api("POST", f"/api/annotation/batches/{batch_id}/state", json=body)


@mcp.tool()
def apply_annotation_alignments(batch_id: int, label: str = "") -> dict:
    """CO_M 应用：把对齐结果写入全局概念/算子并生成版本草稿。"""
    return api("POST", f"/api/annotation/batches/{batch_id}/apply",
               json={"label": label or None})


@mcp.tool()
def create_annotation_version(batch_id: int, label: str, note: str = "",
                              parent_version_id: int | None = None,
                              import_batch_id: int | None = None) -> dict:
    """创建本体版本草稿。"""
    return api("POST", f"/api/annotation/batches/{batch_id}/versions", json={
        "label": label, "note": note, "parent_version_id": parent_version_id,
        "import_batch_id": import_batch_id,
    })


@mcp.tool()
def freeze_annotation_version(version_id: int) -> dict:
    """冻结本体版本（需校验通过）。"""
    return api("POST", f"/api/annotation/versions/{version_id}/freeze")


@mcp.tool()
def release_annotation_version(version_id: int) -> dict:
    """发布已冻结的本体版本（项目负责人/admin）。"""
    return api("POST", f"/api/annotation/versions/{version_id}/release")


@mcp.tool()
def add_annotation_cross_review(batch_id: int, conclusion: str = "pass",
                                scope: str = "", findings: list[str] | None = None) -> dict:
    """记录交叉检查结论（pass/changes/block）。"""
    return api("POST", f"/api/annotation/batches/{batch_id}/cross-review", json={
        "scope": scope, "conclusion": conclusion, "findings": findings or [],
    })


# ---------------- Agent JSON API: 全局规则/个体 ----------------

@mcp.tool()
def list_rules(q: str = "", status: str = "", domain: str = "",
               page: int = 1, size: int = 50) -> dict:
    """分页检索全局规则。"""
    return api("GET", "/api/rules", params={
        "q": q, "status": status, "domain": domain, "page": page, "size": size,
    })


@mcp.tool()
def get_rule(rule_id: int) -> dict:
    """读取单条全局规则。"""
    return api("GET", f"/api/rules/{rule_id}")


@mcp.tool()
def create_rule(name: str, head: list[dict], body: dict | None = None,
                domain: str = "通用", description: str = "",
                sources: list[str] | None = None, status: str = "approved",
                source_uri: str = "", ontology_module: str = "") -> dict:
    """创建全局规则；body/head 为 spec_v1 节点，结构引用用 OMS ID。"""
    return api("POST", "/api/rules", json={
        "name": name, "domain": domain, "description": description,
        "body": body, "head": head, "sources": sources or [], "status": status,
        "source_uri": source_uri or None, "ontology_module": ontology_module or None,
    })


@mcp.tool()
def update_rule(rule_id: int, name: str = "", domain: str = "",
                description: str = "", body: dict | None = None,
                head: list[dict] | None = None, sources: list[str] | None = None,
                status: str = "", source_uri: str = "",
                ontology_module: str = "") -> dict:
    """局部更新全局规则；只传需要变更的字段。"""
    patch = {}
    for key, value in (("name", name), ("domain", domain),
                       ("description", description), ("status", status)):
        if value:
            patch[key] = value
    if body is not None:
        patch["body"] = body
    if head is not None:
        patch["head"] = head
    if sources is not None:
        patch["sources"] = sources
    if source_uri:
        patch["source_uri"] = source_uri
    if ontology_module:
        patch["ontology_module"] = ontology_module
    return api("PATCH", f"/api/rules/{rule_id}", json=patch)


@mcp.tool()
def list_individuals(q: str = "", status: str = "", domain: str = "",
                     concept_id: int | None = None, page: int = 1,
                     size: int = 50) -> dict:
    """分页检索全局个体。"""
    params = {"q": q, "status": status, "domain": domain,
              "page": page, "size": size}
    if concept_id is not None:
        params["concept_id"] = concept_id
    return api("GET", "/api/individuals", params=params)


@mcp.tool()
def get_individual(individual_id: int) -> dict:
    """读取单个全局个体。"""
    return api("GET", f"/api/individuals/{individual_id}")


@mcp.tool()
def create_individual(name: str, concept_id: int | None = None, concept: str = "",
                      domain: str = "通用", description: str = "",
                      sources: list[str] | None = None, status: str = "approved",
                      source_uri: str = "", ontology_module: str = "") -> dict:
    """创建全局个体；concept_id 或 concept 名称二选一。"""
    return api("POST", "/api/individuals", json={
        "name": name, "concept_id": concept_id, "concept": concept or None,
        "domain": domain, "description": description, "sources": sources or [],
        "status": status, "source_uri": source_uri or None,
        "ontology_module": ontology_module or None,
    })


@mcp.tool()
def update_individual(individual_id: int, name: str = "", concept_id: int | None = None,
                      concept: str = "", domain: str = "", description: str = "",
                      sources: list[str] | None = None, status: str = "",
                      source_uri: str = "", ontology_module: str = "") -> dict:
    """局部更新全局个体。"""
    patch = {}
    for key, value in (("name", name), ("domain", domain),
                       ("description", description), ("status", status)):
        if value:
            patch[key] = value
    if concept_id is not None:
        patch["concept_id"] = concept_id
    if concept:
        patch["concept"] = concept
    if sources is not None:
        patch["sources"] = sources
    if source_uri:
        patch["source_uri"] = source_uri
    if ontology_module:
        patch["ontology_module"] = ontology_module
    return api("PATCH", f"/api/individuals/{individual_id}", json=patch)


# ---------------- Agent JSON API: 本体建议箱 ----------------

@mcp.tool()
def list_ontology_suggestions(status: str = "") -> dict:
    """列出 KEOL/agent 提交的本体建议（pending/approved/rejected/failed）。"""
    return api("GET", "/api/keol/suggestions", params={"status": status})


@mcp.tool()
def get_ontology_suggestion(suggestion_id: int) -> dict:
    """读取建议包详情（含 package/refs 与审批统计）。"""
    return api("GET", f"/api/keol/suggestions/{suggestion_id}")


@mcp.tool()
def approve_ontology_suggestion(suggestion_id: int) -> dict:
    """审批通过并应用到全局本体（reviewer/admin）。"""
    return api("POST", f"/api/keol/suggestions/{suggestion_id}/approve")


@mcp.tool()
def reject_ontology_suggestion(suggestion_id: int, note: str = "") -> dict:
    """驳回建议（reviewer/admin）。"""
    return api("POST", f"/api/keol/suggestions/{suggestion_id}/reject",
               json={"note": note})


# ---------------- P2: 搜索 / 版本快照 / 可移植导出 ----------------

@mcp.tool()
def search_ontology(q: str, types: str = "", limit: int = 20) -> dict:
    """跨概念/算子/规则/个体搜索；types 可选逗号分隔。"""
    return api("GET", "/api/search", params={"q": q, "types": types, "limit": limit})


@mcp.tool()
def get_annotation_version_payload(version_id: int) -> dict:
    """读取冻结/已发布版本的完整 payload（用于钉版本拉取/审计）。"""
    return api("GET", f"/api/annotation/versions/{version_id}/payload")


@mcp.tool()
def diff_annotation_versions(version_id: int, base_version_id: int | None = None) -> dict:
    """比较目标版本与基准版本（缺省父版本）。"""
    params = {}
    if base_version_id is not None:
        params["base_version_id"] = base_version_id
    return api("GET", f"/api/annotation/versions/{version_id}/diff", params=params)


@mcp.tool()
def rollback_annotation_version(version_id: int) -> dict:
    """生成指向旧快照的回滚草稿（不覆盖全局）。"""
    return api("POST", f"/api/annotation/versions/{version_id}/rollback")


@mcp.tool()
def export_portable_ontology() -> dict:
    """带概念/算子 ID 的完整导出，供规则跨实例 remap。"""
    return api("GET", "/api/export/portable")


@mcp.tool()
def ops_summary() -> dict:
    """OMS 运行状态：DB/备份/证书/Token/待审建议/端口。"""
    return api("GET", "/api/ops/summary")
