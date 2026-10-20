"""概念图：/graph（页面）与 /api/graph（结构数据，agent 可用）"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..graphviz import build_forest, layout
from ..models import Concept
from ..security import current_user, require_role
from ..templating import templates

router = APIRouter(tags=["graph"])


def _graph_data(db: Session, root_id: int | None, status: str | None = None,
                domain: str | None = None):
    stmt = select(Concept).order_by(Concept.id)
    if status:
        stmt = stmt.where(Concept.status == status)
    if domain:
        stmt = stmt.where(Concept.domain == domain)
    concepts = db.scalars(stmt).all()
    forest = build_forest(concepts)
    return layout(forest, root_id=root_id), forest


@router.get("/api/graph")
def api_graph(root_id: int | None = None, status: str | None = None,
              domain: str | None = None,
              db: Session = Depends(get_db), user: dict = Depends(require_role())):
    data, forest = _graph_data(db, root_id, status, domain)
    if root_id is not None and root_id not in forest:
        return {"nodes": [], "edges": [], "error": f"根概念 #{root_id} 不存在"}
    # 结构 + 布局一起返回（结构可直接用于其他可视化）
    data["links"] = [
        {"source": n["id"], "target": cid}
        for n in forest.values() for cid in n["child_ids"]
        if root_id is None or any(x["id"] == n["id"] for x in data["nodes"])
    ]
    return data


@router.get("/graph")
def graph_page(request: Request, root_id: int | None = None, status: str | None = None,
               domain: str | None = None,
               db: Session = Depends(get_db), user: dict = Depends(current_user)):
    data, forest = _graph_data(db, root_id, status, domain)
    all_concepts = db.scalars(select(Concept).order_by(Concept.id)).all()
    roots = [c for c in all_concepts if c.parent_id is None]
    domains = sorted({(c.domain or "通用") for c in all_concepts})
    return templates.TemplateResponse(request=request, name="graph.html", context={
        "request": request, "user": user, "g": data,
        "root_id": root_id, "status": status, "roots": roots,
        "domain": domain, "domains": domains,
    })
