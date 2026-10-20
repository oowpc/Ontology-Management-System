"""Agent 可发现性：GET /api/agent/manifest

活路由表（运行时内省，永不缺漏）+ 当前工况词表 + 常用工作流配方 + 不变量。
静态文档见仓库根目录 AGENTS.md；OpenAPI 在 /openapi.json。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..gold.service import SLOTS, STAGE_LABELS, STAGES, list_groups as gold_groups
from ..llm import enabled as llm_enabled
from ..security import require_role

router = APIRouter(prefix="/api/agent", tags=["agent"])

WORKFLOWS = [
    {
        "name": "新概念提交→审核→回填",
        "steps": [
            "GET /api/similarity/check?name=X —— 先查重（>=block_threshold 禁止直接入库）",
            "POST /api/submissions {name, aliases, definition, suggested_parent_id, reason, sample_question} —— 发 TMP-xxxx",
            "POST /api/submissions/{id}/approve|reject|merge {comment}（reviewer/admin）—— 通过后 TMP resolved",
            "POST /api/tmp/backfill {text, source_label} —— 文本中的 TMP-xxxx 批量替换为正式概念名，回填记录可查",
        ],
    },
    {
        "name": "GOLDKE 篇级推进",
        "steps": [
            "GET /api/gold/articles?domain=&group_name=&stage= —— 篇任务列表（阶段：CO标注与对齐→大本体整理→KE标注与对齐→已完成）",
            "POST /api/gold/articles/{id}/advance —— 推进到下一阶段",
            "PATCH /api/gold/articles/{id} {ke_done} —— 更新完成条数",
            "GET /api/gold/stats —— 800/400 目标进度、按领域/组别/阶段汇总",
        ],
    },
    {
        "name": "KE 上传与复核",
        "steps": [
            "POST /api/ke/upload（multipart CSV，全量上传；幂等键=组+题目ID）",
            "GET /api/ke/review/queue —— 待复核队列（reviewer/admin）",
            "POST /api/ke/review 或 /api/ke/review/batch —— 打标写回并留痕",
            "GET /api/ke/groups —— 各组今日上传状态",
        ],
    },
    {
        "name": "本体迁移与体检",
        "steps": [
            "GET /api/export/json —— 全量导出（名称自然键，可移植）",
            "POST /api/import/json —— 幂等 upsert（重复导入零变化）",
            "POST /api/health/run → GET /api/health/latest —— 四类体检（未使用/孤立/近义对/算子计数）",
            "GET /api/history —— 全部写操作的 before/after 快照；POST /api/concepts/{id}/rollback/{audit_id} 回滚（admin）",
        ],
    },
]

INVARIANTS = {
    "auth": "Authorization: Bearer <token>（POST /api/tokens 自助签发，明文仅返回一次；代签需 admin）",
    "errors": "统一 {\"detail\": \"…\"}；400 参数 / 401 未认证 / 403 越权 / 404 不存在 / 409 幂等或重名冲突 / 422 校验",
    "idempotency_keys": {
        "concepts/operators": "名称自然键（重名 409）",
        "ke_upload": "组 + 题目ID（重复上传取最新）",
        "gold_items": "篇编号 + KE编号",
        "import": "重复导入全部「未变」零新增",
    },
    "submission_required": "reason + sample_question + suggested_parent_id 三项必填；提交即查重留快照",
    "review_required": "审核意见必填；merge 需 merge_target_id",
    "roles": "annotator 读+提交 / reviewer +审核+编辑 / admin +导入导出+回滚+用户管理",
    "tmp_backfill": "仅 resolved / merged 的 TMP 会被替换；其余列入未决清单",
}


def _iter_routes(app):
    """遍历全部路由（新版 Starlette 的 include_router 为懒加载 _IncludedRouter，
    需经 original_router 展开；Mount/静态目录无 methods 自动跳过）。"""
    def walk(routes, prefix=""):
        for r in routes:
            orig = getattr(r, "original_router", None)
            if orig is not None:
                yield from walk(orig.routes, prefix + (getattr(orig, "prefix", "") or ""))
                continue
            path = getattr(r, "path", "")
            methods = getattr(r, "methods", None)
            if path and methods:
                yield prefix + path, methods
    yield from walk(app.routes)


@router.get("/manifest")
def manifest(request: Request, db: Session = Depends(get_db),
             user: dict = Depends(require_role())):
    app = request.app
    endpoints: dict[str, list[str]] = {}
    for path, methods in _iter_routes(app):
        if not path.startswith("/api/"):
            continue
        ms = sorted(set(methods) - {"HEAD", "OPTIONS"})
        if not ms:
            continue
        seg = path.split("/")[2] if len(path.split("/")) > 2 else "root"
        endpoints.setdefault(seg, []).append(f"{'|'.join(ms)} {path}")
    for v in endpoints.values():
        v.sort()
    return {
        "service": "本体管理系统（Ontology Management System）",
        "version": app.version,
        "auth": INVARIANTS["auth"],
        "docs": {"openapi": "/openapi.json", "swagger": "/docs", "manual": "AGENTS.md（仓库根目录）"},
        "endpoint_count": sum(len(v) for v in endpoints.values()),
        "endpoints": endpoints,
        "workflows": WORKFLOWS,
        "invariants": INVARIANTS,
        "vocab": {
            "gold_stages": [{"key": s, "label": STAGE_LABELS.get(s, s)} for s in STAGES],
            "gold_slots": SLOTS,
            "gold_groups": gold_groups(db),
            "gold_targets": {
                "baseline": settings.gold_baseline,
                "target_total": settings.gold_target_total,
                "target_new": settings.gold_target_new,
            },
            "ke_status_map": settings.ke_status_map,
            "ke_column_map": settings.ke_column_map,
            "similarity": {
                "block_threshold": settings.similarity_block_threshold,
                "suggest_threshold": settings.similarity_suggest_threshold,
            },
            "llm_enabled": llm_enabled(),
        },
    }
