"""LLM 接入（OpenAI 兼容端点）+ 本体管理场景封装。

配置（环境变量；未配置则全部功能优雅降级，返回 None / llm=false）：
  LLM_BASE_URL  如 https://api.deepseek.com/v1 或内网 vLLM / Ollama 地址
  LLM_API_KEY
  LLM_MODEL     如 deepseek-chat / qwen2.5-72b-instruct
  LLM_TIMEOUT   秒，默认 45

场景：
  semantic_dedup   语义查重：编辑距离宽筛 + 同父兄弟 → LLM 判定 same/near/distinct
  suggest_review   审核建议：动作 + 意见草稿 + 合并目标
"""
from __future__ import annotations

import json
import time

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Concept, Submission
from .similarity import find_similar

# M23：429/5xx/网络错误指数退避重试（口径参考 NL2KE kelab/llm.py）
RETRIABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class _HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class _RetriableError(Exception):
    pass


def enabled() -> bool:
    return bool(settings.llm_base_url and settings.llm_model)


def _record_usage(scene: str, *, latency_ms: int, attempts: int, ok: bool,
                  prompt_tokens=None, completion_tokens=None, error: str = ""):
    """调用台账落库；任何失败静默（绝不阻断主流程）。"""
    try:
        from .db import SessionLocal
        from .models import LlmCall
        db = SessionLocal()
        try:
            db.add(LlmCall(scene=scene, model=settings.llm_model,
                           prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                           latency_ms=latency_ms, attempts=attempts, ok=ok,
                           error=(error or "")[:250]))
            db.commit()
        finally:
            db.close()
    except Exception:
        pass


def _post_once(messages: list[dict], *, temperature: float, max_tokens: int, timeout: float):
    """单次请求 → (content, usage)。HTTP 错误按状态分类抛出。"""
    try:
        resp = httpx.post(
            settings.llm_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key or 'none'}"},
            json={"model": settings.llm_model, "messages": messages,
                  "temperature": temperature, "max_tokens": max_tokens},
            timeout=timeout,
        )
    except (httpx.HTTPError, TimeoutError) as exc:
        raise _RetriableError(f"网络错误：{exc}") from exc
    if resp.status_code >= 400:
        raise _HttpError(resp.status_code, resp.text[:200])
    data = resp.json()
    return data["choices"][0]["message"]["content"], (data.get("usage") or {})


def chat_with_usage(messages: list[dict], *, scene: str = "", temperature=0.2,
                    max_tokens=1200, timeout=None) -> dict | None:
    """带重试的调用：返回 {content, prompt_tokens, completion_tokens, latency_ms, attempts}。

    可重试错误（429/5xx/网络）指数退避（1s/2s/4s…封顶 8s），4xx 立即放弃；
    彻底失败返回 None（优雅降级口径不变）。每次调用（含失败）落 llm_calls 台账。
    """
    if not enabled():
        return None
    attempts, error = 0, ""
    started = time.perf_counter()
    while attempts <= settings.llm_max_retries:
        attempts += 1
        try:
            content, usage = _post_once(messages, temperature=temperature,
                                        max_tokens=max_tokens,
                                        timeout=timeout or settings.llm_timeout)
            latency = int((time.perf_counter() - started) * 1000)
            result = {"content": content,
                      "prompt_tokens": usage.get("prompt_tokens"),
                      "completion_tokens": usage.get("completion_tokens"),
                      "latency_ms": latency, "attempts": attempts}
            _record_usage(scene, latency_ms=latency, attempts=attempts, ok=True,
                          prompt_tokens=result["prompt_tokens"],
                          completion_tokens=result["completion_tokens"])
            return result
        except _HttpError as exc:
            error = f"HTTP {exc.status}: {exc}"
            if exc.status not in RETRIABLE_STATUS or attempts > settings.llm_max_retries:
                break
        except _RetriableError as exc:
            error = str(exc)
            if attempts > settings.llm_max_retries:
                break
        except Exception as exc:  # 解析错误等：不重试
            error = str(exc)
            break
        time.sleep(min(2 ** (attempts - 1), 8))
    latency = int((time.perf_counter() - started) * 1000)
    _record_usage(scene, latency_ms=latency, attempts=attempts, ok=False, error=error)
    return None


def chat(messages: list[dict], *, scene: str = "", temperature=0.2, max_tokens=1200,
         timeout=None) -> str | None:
    """OpenAI 兼容 /chat/completions；任何失败返回 None（绝不阻断主流程）。"""
    result = chat_with_usage(messages, scene=scene, temperature=temperature,
                             max_tokens=max_tokens, timeout=timeout)
    return result["content"] if result else None


def chat_json(messages: list[dict], *, scene: str = "", **kw) -> dict | None:
    """期望 JSON 输出；容错解析（剥 ```json 围栏、截取首尾花括号）。"""
    text = chat(messages, scene=scene, **kw)
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


# ---------- 场景 1：语义查重 ----------

def _concept_line(c: Concept) -> str:
    alias = f"（别名：{'、'.join(c.aliases)}）" if c.aliases else ""
    return f"#{c.id} {c.name}{alias}——{c.definition or '无定义'}"


def semantic_dedup(db: Session, *, name: str, aliases, parent_id=None, limit=8) -> list[dict] | None:
    """宽筛候选（编辑距离低阈值 + 同父兄弟）→ LLM 判定；返回 None 表示不可用。"""
    if not enabled():
        return None
    candidates = {c["id"]: c for c in find_similar(
        db, name=name, aliases=aliases, parent_id=parent_id, limit=12, threshold=0.3)}
    if parent_id is not None:
        siblings = db.scalars(select(Concept).where(
            Concept.parent_id == parent_id, Concept.status == "approved").limit(10)).all()
        for s in siblings:
            candidates.setdefault(s.id, {"id": s.id})
    if not candidates:
        return []
    concepts = db.scalars(select(Concept).where(Concept.id.in_(candidates.keys()))).all()
    parent_name = None
    if parent_id:
        p = db.get(Concept, parent_id)
        parent_name = p.name if p else None
    lines = "\n".join(_concept_line(c) for c in concepts)
    data = chat_json([
        {"role": "system", "content": "你是中文政务领域本体库的查重专家。只输出 JSON，不要输出其他内容。"},
        {"role": "user", "content": (
            f"待入库概念：名称「{name}」，别名 {list(aliases or [])}，拟定父类「{parent_name or '无'}」。\n"
            f"库中候选概念：\n{lines}\n\n"
            "逐个判断候选与待入库概念的语义关系：\n"
            "- same：语义同义，应合并而非新建\n"
            "- near：近义或强相关，新建需审核注意\n"
            "- distinct：不同概念\n"
            '输出 JSON：{"items":[{"id": 候选编号数字, "verdict": "same|near|distinct", "reason": "不超过30字"}]}'
        )},
    ], scene="semantic_dedup")
    if not data or not isinstance(data.get("items"), list):
        return None
    by_id = {c.id: c for c in concepts}
    out = []
    for item in data["items"]:
        c = by_id.get(item.get("id"))
        verdict = item.get("verdict")
        if not c or verdict not in ("same", "near", "distinct"):
            continue
        out.append({"id": c.id, "name": c.name, "aliases": c.aliases or [],
                    "verdict": verdict, "reason": str(item.get("reason", ""))[:100]})
    order = {"same": 0, "near": 1, "distinct": 2}
    out.sort(key=lambda x: order[x["verdict"]])
    return out[:limit]


# ---------- 场景 2：审核建议 ----------

def suggest_review(db: Session, submission_id: int) -> dict | None:
    if not enabled():
        return None
    sub = db.get(Submission, submission_id)
    if not sub:
        return None
    c = db.get(Concept, sub.concept_id)
    parent = db.get(Concept, sub.suggested_parent_id) if sub.suggested_parent_id else None
    siblings = db.scalars(select(Concept.name).where(
        Concept.parent_id == sub.suggested_parent_id,
        Concept.status == "approved").limit(30)).all() if parent else []
    snapshot = "\n".join(
        f"- {h['name']}（相似度 {h['score']:.0%}，{h.get('matched_on', '')}命中）"
        for h in (sub.similar_snapshot or [])[:8]
    ) or "（无相似候选）"
    data = chat_json([
        {"role": "system", "content": "你是本体库入库审核员。只输出 JSON，不要输出其他内容。"},
        {"role": "user", "content": (
            f"待审概念：名称「{c.name}」，别名 {c.aliases or []}，定义「{c.definition or '无'}」。\n"
            f"建议父类：「{parent.name if parent else '无'}」，该父类下已有子类：{list(siblings)[:20]}。\n"
            f"提交理由：{sub.reason}\n附题目：{sub.sample_question}\n"
            f"查重快照：\n{snapshot}\n\n"
            "给出审核建议：approve=可入库、reject=应驳回、merge=与库中某概念同义应合并。\n"
            '输出 JSON：{"action":"approve|reject|merge","comment":"可直接填写的审核意见（50字内）",'
            '"merge_target":"合并目标概念名或 null","rationale":"判断依据（30字内）"}'
        )},
    ], scene="suggest_review")
    if not data or data.get("action") not in ("approve", "reject", "merge"):
        return None
    merge_target_id = None
    if data.get("action") == "merge" and data.get("merge_target"):
        t = db.scalar(select(Concept).where(Concept.name == str(data["merge_target"]).strip()))
        if t:
            merge_target_id = t.id
    return {
        "action": data["action"],
        "comment": str(data.get("comment", ""))[:200],
        "merge_target_id": merge_target_id,
        "merge_target": data.get("merge_target") if merge_target_id else None,
        "rationale": str(data.get("rationale", ""))[:100],
    }
