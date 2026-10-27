"""Playwright 截图回归：登录 → 关键页面截图 + 交互断言。

建议用 scripts/ui_smoke.sh 启动临时库/临时端口后运行，避免碰生产。
需要 ~/playwright-venv 与可用的 Chromium（默认用已有 ms-playwright 缓存）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="OMS 前端截图回归")
    ap.add_argument("--base-url", default="http://127.0.0.1:8021")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--pass", dest="password", default="admin")
    ap.add_argument("--out", default="/tmp/oms-ui-smoke")
    ap.add_argument("--chromium", default="")
    ap.add_argument("--baseline-dir", default=str(Path.home() / "oms-ui-screenshots" / "baseline"))
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--max-diff-ratio", type=float, default=0.001)
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    baseline = Path(args.baseline_dir)
    diffs = out / "diffs"
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = ""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            print(f"[FAIL] {name} {detail}")

    def image_diff(current: Path, base: Path) -> tuple[float, str]:
        from PIL import Image, ImageChops
        a = Image.open(current).convert("RGB")
        b = Image.open(base).convert("RGB")
        if a.size != b.size:
            return 1.0, f"size {a.size} != baseline {b.size}"
        diff = ImageChops.difference(a, b).convert("L")
        hist = diff.histogram()
        bad = sum(hist[9:])
        total = a.width * a.height
        ratio = bad / total if total else 0.0
        if ratio > 0:
            diffs.mkdir(parents=True, exist_ok=True)
            mask = diff.point(lambda v: 255 if v > 8 else 0)
            red = Image.new("RGB", a.size, (255, 0, 0))
            Image.composite(red, b, mask).save(diffs / current.name)
        return ratio, ""

    exe = args.chromium or ""
    if not exe:
        candidates = sorted(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux*/chrome"))
        exe = str(candidates[-1]) if candidates else ""
    if not exe or not Path(exe).exists():
        print("未找到 Chromium；请设置 --chromium 或安装 playwright chromium")
        return 2

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = ctx.new_page()
        page.set_default_timeout(15000)
        console_errors: list[str] = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))

        def flush_console(name: str):
            errs = [e for e in console_errors if "favicon" not in e.lower()]
            check(f"console clean {name}", not errs, "; ".join(errs[:3]))
            console_errors.clear()

        def goto(path: str):
            resp = page.goto(args.base_url + path, wait_until="networkidle")
            check(f"HTTP {path}", resp is None or resp.status < 400,
                  str(resp.status if resp else ""))
            return resp

        def shot(name: str):
            path = out / name
            page.screenshot(path=str(path), full_page=True)
            base = baseline / name
            if args.update_baseline:
                baseline.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, base)
                check(f"baseline {name}", True, "updated")
            elif not base.exists():
                check(f"baseline {name}", False, "baseline missing; run with --update-baseline")
            else:
                ratio, why = image_diff(path, base)
                check(f"pixel diff {name}", ratio <= args.max_diff_ratio,
                      why or f"diff={ratio:.6f} > {args.max_diff_ratio}")
            return path

        page.goto(args.base_url + "/login", wait_until="domcontentloaded")
        page.fill('input[name="username"]', args.user)
        page.fill('input[name="password"]', args.password)
        page.click('button[type="submit"]')
        try:
            page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        except Exception:
            pass
        check("login", "/login" not in page.url, page.url)
        flush_console("login")

        def api_get(path: str, **params):
            r = page.request.get(args.base_url + path, params=params)
            return r.json() if r.ok else {}

        found = api_get("/api/concepts", q="ExplicitTimePeriod", size=5).get("items", [])
        tid = next((i["id"] for i in found if i["name"] == "ExplicitTimePeriod"), None)
        check("find ExplicitTimePeriod", tid is not None, str(tid))
        ti = api_get("/api/concepts", q="TimeInterval", size=5).get("items", [])
        time_interval = next((i["id"] for i in ti if i["name"] == "TimeInterval"), None)

        if tid:
            goto(f"/concepts/{tid}")
            shot("concept-detail.png")
            flush_console("concept-detail")
            body = page.content()
            check("detail has 2 parents", "2 个父类" in body)
            check("detail has ProperInterval", "ProperInterval" in body)
            check("detail has source", "来源" in body and "source-uri" in body)

            goto(f"/concepts/{tid}/edit")
            shot("concept-edit.png")
            flush_console("concept-edit")
            edit = page.content()
            check("edit has pickers", page.locator(".picker").count() >= 2)
            check("edit no huge select", 'select name="parent_id"' not in edit)
            try:
                page.locator("#picker-primary .picker-input").click()
                page.locator("#picker-primary .picker-input").fill("Time")
                page.wait_for_selector("#picker-primary .picker-result", timeout=8000)
                shot("concept-edit-picker.png")
                check("picker search results", page.locator("#picker-primary .picker-result").count() > 0)
            except Exception as e:  # noqa: BLE001
                check("picker search results", False, str(e)[:160])

            goto("/concepts?q=ExplicitTimePeriod")
            shot("concept-list.png")
            flush_console("concept-list")
            check("list +N popover", page.locator(".parent-more").count() > 0)
            try:
                page.locator(".parent-more summary").first.click()
                page.wait_for_timeout(150)
                shot("concept-list-popover.png")
                check("list popover visible", page.locator(".parent-pop").first.is_visible())
            except Exception as e:  # noqa: BLE001
                check("list popover visible", False, str(e)[:160])

        if time_interval:
            goto(f"/graph?root_id={time_interval}&layout=dag")
            shot("graph-dag.png")
            flush_console("graph-dag")
            check("graph dag attr", page.locator('svg[data-layout="dag"]').count() == 1)
            check("graph extra edges", page.locator("path.edge.extra").count() > 0)
            check("graph legend", "次父类（虚线）" in page.content())

        goto("/submit")
        shot("submit.png")
        flush_console("submit")
        check("submit has pickers", page.locator(".picker").count() >= 2)

        goto("/operators?page=1")
        shot("operators.png")
        flush_console("operators")
        check("operators paginated", page.locator(".pager").count() > 0)
        check("operators picker", page.locator('.picker[data-name="subject_concept_id"]').count() == 1)

        goto("/review?status=pending")
        shot("review.png")
        flush_console("review")

        goto("/annotation")
        shot("annotation-list.png")
        flush_console("annotation")

        browser.close()

    report = {"base_url": args.base_url, "checks": checks,
              "passed": sum(1 for c in checks if c["ok"]), "total": len(checks),
              "baseline_dir": str(baseline), "update_baseline": args.update_baseline,
              "max_diff_ratio": args.max_diff_ratio,
              "screenshots": sorted(str(p) for p in out.glob("*.png")),
              "diffs": sorted(str(p) for p in diffs.glob("*.png")) if diffs.exists() else []}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [c for c in checks if not c["ok"]]
    print(json.dumps({k: report[k] for k in ("passed", "total", "screenshots")},
                     ensure_ascii=False, indent=2))
    for f in failed:
        print(f"  FAIL {f['name']}: {f['detail']}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
