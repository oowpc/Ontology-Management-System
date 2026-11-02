"""KE2SQL 桥：KE → SQL，并在 Archer SQLite 只读执行。"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("KE2SQL_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
API_KEY = os.environ.get("KE2SQL_API_KEY", "").strip()
TIMEOUT = float(os.environ.get("KE2SQL_TIMEOUT", "30"))


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if API_KEY:
        h["X-API-Key"] = API_KEY
    return h


def _req(method: str, path: str, body: dict) -> tuple[int, dict | str]:
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=_headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:1000]
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def _ont_id(db_id: str) -> str:
    safe = "oms__archer__" + db_id
    return "".join(c if (c.isalnum() or c in "._-") else "_" for c in safe)


def translate(ke: str, ontology: dict, db_id: str) -> dict:
    oid = _ont_id(db_id)
    status, body = _req("PUT", f"/ontologies/{oid}", {"ontology": ontology})
    if status not in (200, 201):
        return {"ok": False, "sql": "", "error": f"KE2SQL 注册失败 HTTP {status}: {body}"}
    status, body = _req("POST", "/translations", {
        "ontology_id": oid, "ke": ke, "dialect": "sqlite"})
    if status != 201 or not isinstance(body, dict):
        return {"ok": False, "sql": "", "error": f"KE2SQL 翻译失败 HTTP {status}: {body}"}
    return {"ok": True, "sql": str(body.get("sql") or ""),
            "warnings": body.get("warnings") or [],
            "diagnostics": body.get("diagnostics"), "error": None}


def execute(path: str, sql: str, *, schema: str | None = None,
            max_rows: int = 100, timeout: float = 5.0) -> dict:
    if not path or not Path(path).exists():
        return {"ok": False, "error": "该数据库没有可用的 SQLite 文件"}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    deadline = time.monotonic() + timeout
    try:
        con.execute("PRAGMA query_only=ON")
        if schema:
            con.execute(f'ATTACH DATABASE ? AS "{schema}"',
                        (f"file:{path}?mode=ro",))
        con.set_progress_handler(
            lambda: 1 if time.monotonic() > deadline else 0, 10000)
        cur = con.execute(sql)
        cols = [d[0] for d in (cur.description or [])]
        rows = cur.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        out = []
        for row in rows:
            out.append([v.hex() if isinstance(v, bytes) else
                        (v.isoformat() if hasattr(v, "isoformat") else v) for v in row])
        return {"ok": True, "sql": sql, "columns": cols, "rows": out,
                "row_count": len(out), "truncated": truncated, "error": None}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "sql": sql, "error": str(e)[:1000],
                "columns": [], "rows": [], "row_count": 0, "truncated": False}
    finally:
        con.close()


def run(ke: str, ontology: dict, db_id: str, sqlite_path: str | None) -> dict:
    t0 = time.monotonic()
    tr = translate(ke, ontology, db_id)
    result = {"translate": tr, "execution": None,
              "latency_ms": int((time.monotonic() - t0) * 1000)}
    if tr.get("ok") and tr.get("sql"):
        result["execution"] = execute(sqlite_path or "", tr["sql"], schema=db_id)
        result["latency_ms"] = int((time.monotonic() - t0) * 1000)
    return result
