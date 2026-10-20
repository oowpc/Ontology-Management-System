"""MCP stdio server：把本体管理系统 API 暴露为 MCP 工具，agent 直连操控。

启动（两种认证）：
  ONTOLOGY_TOKEN=ke_... python scripts/mcp_server.py
  或先拿 session 自助签发：见 AGENTS.md §1

环境变量：
  ONTOLOGY_BASE_URL  默认 http://127.0.0.1:8000
  ONTOLOGY_TOKEN     Bearer Token（POST /api/tokens 签发）

Claude Code 接入示例（.mcp.json）：
  {"mcpServers": {"ontology": {"command": "python", "args": ["scripts/mcp_server.py"],
    "env": {"ONTOLOGY_TOKEN": "ke_..."}}}}
"""
from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get("ONTOLOGY_BASE_URL", "http://127.0.0.1:8000")
TOKEN = os.environ.get("ONTOLOGY_TOKEN", "")

mcp = FastMCP("ontology-manager")

_client = None


def build_client() -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {TOKEN}"},
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
def list_issues(status: str = "", cluster: str = "") -> dict:
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


if __name__ == "__main__":
    mcp.run()  # stdio
