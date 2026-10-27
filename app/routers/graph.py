"""概念图：/graph（页面）与 /api/graph（结构数据，agent 可用）"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..graphviz import build_forest, layout, layout_dag
from ..models import Concept, ConceptParent
from ..security import current_user, require_role
from ..templating import templates

router = APIRouter(tags=["graph"])


def _graph_data(db: Session, root_id: int | None, status: str | None = None,
                domain: str | None = None, layout_mode: str = "tree"):
    stmt = select(Concept).order_by(Concept.id)
    if status:
        stmt = stmt.where(Concept.status == status)
    if domain:
        stmt = stmt.where(Concept.domain == domain)
    concepts = db.scalars(stmt).all()
    ids = {c.id for c in concepts}
    parent_map: dict[int, list[int]] = {}
    for link in db.scalars(select(ConceptParent).order_by(ConceptParent.position)):
        if link.concept_id in ids and link.parent_id in ids:
            parent_map.setdefault(link.concept_id, []).append(link.parent_id)
    forest = build_forest(concepts, parent_map)
    fn = layout_dag if layout_mode == "dag" else layout
    return fn(forest, root_id=root_id), forest


@router.get("/api/graph")
def api_graph(root_id: int | None = None, status: str | None = None,
              domain: str | None = None, layout: str = "tree",
              db: Session = Depends(get_db), user: dict = Depends(require_role())):
    data, forest = _graph_data(db, root_id, status, domain,
                               "dag" if layout == "dag" else "tree")
    if root_id is not None and root_id not in forest:
        return {"nodes": [], "edges": [], "error": f"根概念 #{root_id} 不存在"}
    # 结构 + 布局一起返回（结构可直接用于其他可视化）
    node_ids = {x["id"] for x in data["nodes"]}
    data["links"] = [
        {"source": n["id"], "target": cid, "extra": False}
        for n in forest.values() for cid in n["child_ids"]
        if cid in node_ids
    ] + [
        {"source": pid, "target": n["id"], "extra": True}
        for n in forest.values() for pid in n.get("extra_parent_ids", [])
        if n["id"] in node_ids and pid in node_ids
    ]
    return data


@router.get("/graph")
def graph_page(request: Request, root_id: str | None = None, status: str | None = None,
               domain: str | None = None, layout: str = "dag",
               db: Session = Depends(get_db), user: dict = Depends(current_user)):
    # 空字符串（“整片森林”）不能直接传给 int 参数，否则 422
    rid = int(root_id) if root_id and str(root_id).isdigit() else None
    layout_mode = "dag" if layout == "dag" else "tree"
    data, forest = _graph_data(db, rid, status, domain, layout_mode)
    all_concepts = db.scalars(select(Concept).order_by(Concept.id)).all()
    roots = [c for c in all_concepts if c.parent_id is None]
    if rid is not None and all(c.id != rid for c in roots):
        selected = db.get(Concept, rid)
        if selected:
            roots = [selected] + roots
    domains = sorted({(c.domain or "通用") for c in all_concepts})
    return templates.TemplateResponse(request=request, name="graph.html", context={
        "request": request, "user": user, "g": data,
        "root_id": rid, "status": status, "roots": roots,
        "domain": domain, "domains": domains, "layout": layout_mode,
    })
