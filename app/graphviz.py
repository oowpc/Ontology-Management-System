"""概念继承图布局：森林 → tidy tree 坐标（左→右，根在左）。

纯函数、不依赖 Web 层；SVG 元素数据由模板渲染成 <svg>。
树是单父继承森林；对断链/环做防御（未访问节点按根处理，每节点只布一次）。
"""
from __future__ import annotations

CHAR_W = 13      # CJK 字符近似宽
NODE_H = 26
DY = 34          # 行距
PAD_X = 16       # 节点内边距
GAP_X = 44       # 层间距
MAX_NODES = 500  # 超过则提示先按根过滤


def build_forest(concepts) -> dict:
    """concepts: 含 id/name/parent_id/status/aliases/definition 的对象序列。"""
    nodes = {}
    for c in concepts:
        nodes[c.id] = {
            "id": c.id, "name": c.name, "parent_id": c.parent_id,
            "status": c.status, "aliases": list(c.aliases or []),
            "definition": c.definition or "", "child_ids": [],
        }
    for n in nodes.values():
        if n["parent_id"] in nodes:
            nodes[n["parent_id"]]["child_ids"].append(n["id"])
    for n in nodes.values():
        n["child_ids"].sort(key=lambda i: nodes[i]["name"])
    return nodes


def _label(n: dict) -> str:
    return n["name"] + (f"（{len(n['child_ids'])}）" if n["child_ids"] else "")


def layout(nodes: dict, root_id: int | None = None) -> dict:
    """返回 {nodes:[{id,name,status,x,y,w,child_count,aliases,definition}], edges:[{d}],
    width, height, total, truncated, roots:[{id,name}]}。"""
    if root_id is not None:
        nodes = _subtree(nodes, root_id)
    total = len(nodes)
    roots_all = [n["id"] for n in nodes.values() if n["parent_id"] not in nodes]
    roots_all.sort(key=lambda i: nodes[i]["name"])
    truncated = total > MAX_NODES

    next_y = [0.0]
    depth_of: dict[int, int] = {}
    y_of: dict[int, float] = {}

    def walk(nid: int, depth: int, seen: set) -> float:
        if nid in y_of or nid in seen:
            return y_of.get(nid, 0.0)
        seen.add(nid)
        depth_of[nid] = depth
        children = [c for c in nodes[nid]["child_ids"] if c not in seen]
        if not children:
            y = next_y[0]
            next_y[0] += 1.0
        else:
            ys = [walk(c, depth + 1, seen) for c in children]
            y = sum(ys) / len(ys)
        y_of[nid] = y
        return y

    # 主根优先；断链/环防御：未访问节点补为根
    seen: set = set()
    for rid in roots_all:
        walk(rid, 0, seen)
    for nid in nodes:
        if nid not in y_of:
            walk(nid, 0, seen)

    if not y_of:
        return {"nodes": [], "edges": [], "width": 0, "height": 0,
                "total": 0, "truncated": False, "roots": roots_all}

    # 每层宽度 = 该层最长标签；x 逐层累加
    width_of = {nid: len(_label(nodes[nid])) * CHAR_W + PAD_X * 2 for nid in y_of}
    max_depth = max(depth_of.values())
    depth_w = [0.0] * (max_depth + 1)
    for nid, d in depth_of.items():
        depth_w[d] = max(depth_w[d], width_of[nid])
    depth_x = [0.0] * (max_depth + 2)
    for d in range(1, max_depth + 2):
        depth_x[d] = depth_x[d - 1] + depth_w[d - 1] + GAP_X

    out_nodes, edges = [], []
    for nid, y in y_of.items():
        d = depth_of[nid]
        x = depth_x[d]
        n = nodes[nid]
        out_nodes.append({
            "id": nid, "name": n["name"], "status": n["status"],
            "aliases": n["aliases"], "definition": n["definition"],
            "child_count": len(n["child_ids"]),
            "x": round(x, 1), "y": round(y * DY + 8, 1), "w": round(width_of[nid], 1),
        })
        for cid in n["child_ids"]:
            if cid not in y_of:
                continue
            x1 = x + width_of[nid]
            y1 = y * DY + 8 + NODE_H / 2
            x2 = depth_x[depth_of[cid]]
            y2 = y_of[cid] * DY + 8 + NODE_H / 2
            mid = (x1 + x2) / 2
            edges.append({"d": f"M{x1:.1f},{y1:.1f} H{mid:.1f} V{y2:.1f} H{x2:.1f}"})

    return {
        "nodes": sorted(out_nodes, key=lambda n: (n["x"], n["y"])),
        "edges": edges,
        "width": round(depth_x[max_depth + 1] - GAP_X + PAD_X, 1),
        "height": round((max(y_of.values()) + 1) * DY + 16, 1),
        "total": total, "truncated": truncated, "roots": roots_all,
    }


def _subtree(nodes: dict, root_id: int) -> dict:
    if root_id not in nodes:
        return {}
    keep = {root_id}
    frontier = [root_id]
    while frontier:
        cur = frontier.pop()
        for cid in nodes[cur]["child_ids"]:
            if cid not in keep:
                keep.add(cid)
                frontier.append(cid)
    return {i: dict(n, child_ids=[c for c in n["child_ids"] if c in keep])
            for i, n in nodes.items() if i in keep}
