"""KE-matcher 等价判定桥：参考 KE 与 NL2KE 生成的 KE 对比。"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE_URL = os.environ.get("KE_MATCHER_BASE_URL", "http://127.0.0.1:8082").rstrip("/")
KEVAL_PATH = os.environ.get(
    "KE_MATCHER_KEVAL_PATH", "/home/oowpc/Syntastic_and_Lexical-Verifier")
TIMEOUT = float(os.environ.get("KE_MATCHER_TIMEOUT", "65"))


def _request(path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + path,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:800]
        raise RuntimeError(f"KE-matcher {e.code}: {detail}") from e
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"KE-matcher 请求失败：{e}") from e


def health() -> dict:
    return _request("/health")


def judge(reference_ke: str, candidate_ke: str, *,
          request_timeout_s: float = 60) -> dict:
    """以参考 KE 为 A、候选 KE 为 B 做等价判定。"""
    return _request("/judge", {
        "ke_a": reference_ke,
        "ke_b": candidate_ke,
        "layers": {
            "s1": True,
            "keval_path": KEVAL_PATH,
        },
        "request_timeout_s": max(1.0, min(float(request_timeout_s), 120.0)),
    })


def judge_many(reference_ke: str, candidates: list[str], *,
               request_timeout_s: float = 60) -> dict:
    """批量多候选：以同一参考 KE 对多个候选逐项判定（服务端有界并发）。"""
    items = [str(x) for x in (candidates or []) if str(x).strip()]
    if not items:
        raise ValueError("candidates 不能为空")
    if len(items) > 20:
        raise ValueError("candidates 最多 20 条")
    return _request("/judge/batch", {
        "ke_a": reference_ke,
        "candidates": items,
        "layers": {"s1": True, "keval_path": KEVAL_PATH},
        "request_timeout_s": max(1.0, min(float(request_timeout_s), 120.0)),
    })
