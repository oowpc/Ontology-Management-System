"""前端可用性审计：重复 ID / 横向溢出 / 图谱节点重叠 / 关键交互可点击。

与 ui_smoke.py 共用临时库/临时端口，建议由 ui_smoke.sh 调用。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="OMS 前端可用性审计")
    ap.add_argument("--base-url", default="http://127.0.0.1:8021")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--pass", dest="password", default="admin")
    ap.add_argument("--out", default="/tmp/oms-ui-smoke")
    ap.add_argument("--chromium", default="")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = ""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            print(f"[FAIL] {name} {detail}")

    exe = args.chromium or ""
    if not exe:
        candidates = sorted(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux*/chrome"))
        exe = str(candidates[-1]) if candidates else ""
    if not exe or not Path(exe).exists():
        print("未找到 Chromium；请设置 --chromium")
        return 2

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = ctx.new_page()
        page.set_default_timeout(15000)
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        def flush(name: str):
            errs = [e for e in errors if "favicon" not in e.lower()]
            check(f"console clean {name}", not errs, "; ".join(errs[:3]))
            errors.clear()

        def goto(path: str):
            resp = page.goto(args.base_url + path, wait_until="networkidle")
            check(f"HTTP {path}", resp is None or resp.status < 400,
                  str(resp.status if resp else ""))
            return resp

        def page_issues(name: str):
            dup = page.evaluate("""() => {
                const seen = {};
                document.querySelectorAll('[id]').forEach(e => { seen[e.id] = (seen[e.id] || 0) + 1; });
                return Object.entries(seen).filter(([k, v]) => v > 1);
            }""")
            check(f"no duplicate ids {name}", not dup, json.dumps(dup, ensure_ascii=False))
            overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
            check(f"no page horizontal overflow {name}", not overflow,
                  "" if not overflow else f"scrollWidth={page.evaluate('document.documentElement.scrollWidth')}")
            bad_links = page.evaluate("""() => [...document.querySelectorAll('a')]
                .filter(a => (!a.getAttribute('href') || a.getAttribute('href') === '#') && a.textContent.trim())
                .map(a => a.textContent.trim()).slice(0, 8)""")
            check(f"no empty links {name}", not bad_links, "、".join(bad_links))
            flush(name)

        page.goto(args.base_url + "/login", wait_until="domcontentloaded")
        page.fill('input[name="username"]', args.user)
        page.fill('input[name="password"]', args.password)
        page.click('button[type="submit"]')
        try:
            page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        except Exception:
            pass
        check("login", "/login" not in page.url, page.url)
        flush("login")

        def api_get(path: str, **params):
            r = page.request.get(args.base_url + path, params=params)
            return r.json() if r.ok else {}

        found = api_get("/api/concepts", q="ExplicitTimePeriod", size=5).get("items", [])
        tid = next((i["id"] for i in found if i["name"] == "ExplicitTimePeriod"), None)
        ti = api_get("/api/concepts", q="TimeInterval", size=5).get("items", [])
        time_interval = next((i["id"] for i in ti if i["name"] == "TimeInterval"), None)
        parent = api_get("/api/concepts", q="机构", size=5).get("items", [])
        parent_id = next((i["id"] for i in parent if i["name"] == "机构"), None)
        check("fixtures", all([tid, time_interval, parent_id]), f"tid={tid} ti={time_interval} parent={parent_id}")

        # 详情页：重复 ID / 溢出 / 父类链接
        if tid:
            goto(f"/concepts/{tid}")
            page_issues("concept-detail")
            src = page.locator("a.source-uri").first
            check("source uri present", src.count() == 1 and src.get_attribute("href", timeout=1000) != "")
            parent_link = page.locator("table.kv tr").filter(has_text="父类").locator("a").first
            href = parent_link.get_attribute("href") or ""
            check("parent link present", href.startswith("/concepts/"), href)
            parent_link.click()
            page.wait_for_load_state("networkidle")
            check("parent link navigates", "/concepts/" in page.url, page.url)

        # 编辑页：picker 搜索/选择、取消可返回
        if tid:
            goto(f"/concepts/{tid}/edit")
            page_issues("concept-edit")
            page.locator("#picker-primary .picker-input").click()
            page.locator("#picker-primary .picker-input").fill("Time")
            page.wait_for_selector("#picker-primary .picker-result", timeout=8000)
            page.locator("#picker-primary .picker-result").first.click()
            check("primary picker writes hidden",
                  bool(page.locator('input[name="parent_id"]').input_value()))
            page.locator("#picker-extras .picker-input").click()
            page.locator("#picker-extras .picker-input").fill("Time")
            page.wait_for_selector("#picker-extras .picker-result", timeout=8000)
            page.locator("#picker-extras .picker-result").first.click()
            check("extra picker writes hidden",
                  page.locator('input[name="extra_parent_ids"]').count() >= 1)
            page.locator("a.btn", has_text="取消").click()
            page.wait_for_load_state("networkidle")
            check("cancel returns", "/concepts/" in page.url and "/edit" not in page.url, page.url)

        # 列表：+N 弹层可点、父类筛选可提交
        goto("/concepts?q=ExplicitTimePeriod")
        page_issues("concept-list")
        if page.locator(".parent-more").count():
            page.locator(".parent-more summary").first.click()
            page.wait_for_timeout(100)
            check("list popover opens", page.locator(".parent-pop").first.is_visible())
            pop_link = page.locator(".parent-pop a").first
            href = pop_link.get_attribute("href") or ""
            pop_link.click()
            page.wait_for_load_state("networkidle")
            check("list popover link navigates", href.startswith("/concepts/") and "/concepts/" in page.url, page.url)

        goto("/concepts")
        page.locator("#filter-parent .picker-input").click()
        page.locator("#filter-parent .picker-input").fill("TimePeriod")
        page.wait_for_selector("#filter-parent .picker-result", timeout=8000)
        page.locator("#filter-parent .picker-result").first.click()
        page.locator("button.btn", has_text="过滤").click()
        page.wait_for_load_state("networkidle")
        check("parent filter submits", "parent_id=" in page.url, page.url)
        check("parent filter has rows", page.locator("table.data tbody tr").count() >= 1)

        # 算子分页
        goto("/operators?page=1")
        page_issues("operators")
        if page.locator("a.btn", has_text="下一页").count():
            first_id = page.locator("table.data tbody tr").first.text_content()
            page.locator("a.btn", has_text="下一页").first.click()
            page.wait_for_load_state("networkidle")
            check("operator next page", "page=2" in page.url, page.url)
            check("operator rows changed", page.locator("table.data tbody tr").first.text_content() != first_id)

        # 图谱：节点不重叠、布局切换、节点可点
        if time_interval:
            goto(f"/graph?root_id={time_interval}&layout=dag")
            page_issues("graph-dag")
            boxes = page.evaluate("""() => [...document.querySelectorAll('#concept-graph g.node')].map(g => {
                const r = g.getBoundingClientRect();
                return {name: g.textContent.trim(), x:r.x, y:r.y, w:r.width, h:r.height};
            })""")
            overlaps = []
            for i in range(len(boxes)):
                for j in range(i + 1, len(boxes)):
                    a, b = boxes[i], boxes[j]
                    ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
                    oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
                    if ox > 2 and oy > 2:
                        overlaps.append((a["name"], b["name"], round(ox, 1), round(oy, 1)))
            check("graph nodes no overlap", not overlaps, str(overlaps[:5]))
            check("graph has extra edges", page.locator("path.edge.extra").count() > 0)
            page.select_option('select[name="layout"]', "tree")
            page.locator("button.btn", has_text="过滤").click()
            page.wait_for_load_state("networkidle")
            check("graph tree layout", page.locator('svg[data-layout="tree"]').count() == 1)
            page.select_option('select[name="layout"]', "dag")
            page.locator("button.btn", has_text="过滤").click()
            page.wait_for_load_state("networkidle")
            check("graph dag layout", page.locator('svg[data-layout="dag"]').count() == 1)
            page.locator("#concept-graph g.node").first.click()
            page.wait_for_load_state("networkidle")
            check("graph node clickable", "/concepts/" in page.url, page.url)

        # 提交页 picker
        goto("/submit")
        page_issues("submit")
        page.locator("#picker-suggested-primary .picker-input").click()
        page.locator("#picker-suggested-primary .picker-input").fill("机构")
        page.wait_for_selector("#picker-suggested-primary .picker-result", timeout=8000)
        page.locator("#picker-suggested-primary .picker-result").first.click()
        check("submit primary picker", bool(page.locator('input[name="suggested_parent_id"]').input_value()))
        page.locator("#picker-extra-parents .picker-input").click()
        page.locator("#picker-extra-parents .picker-input").fill("Time")
        page.wait_for_selector("#picker-extra-parents .picker-result", timeout=8000)
        page.locator("#picker-extra-parents .picker-result").first.click()
        check("submit extra picker", page.locator('input[name="extra_parent_ids"]').count() >= 1)

        # 审核页合并 picker：临时库创建一个 pending 提交
        if parent_id:
            body = json.dumps({"name": "审计临时待审概念", "suggested_parent_id": parent_id,
                               "reason": "audit", "sample_question": "audit"}, ensure_ascii=False)
            csrf = page.get_attribute('meta[name="csrf-token"]', "content") or ""
            r = page.request.post(args.base_url + "/api/submissions", data=body,
                                  headers={"Content-Type": "application/json",
                                           "X-CSRF-Token": csrf})
            check("create pending submission", r.ok, str(r.status))
            goto("/review?status=pending")
            page_issues("review")
            merge = page.locator('.picker[data-name="merge_target_id"]').first
            check("review merge picker present", merge.count() == 1)
            if merge.count():
                merge.locator(".picker-input").click()
                merge.locator(".picker-input").fill("机构")
                page.wait_for_selector('.picker[data-name="merge_target_id"] .picker-result', timeout=8000)
                merge.locator(".picker-result").first.click()
                check("review merge picker writes hidden",
                      bool(page.locator('input[name="merge_target_id"]').first.input_value()))

        # 导航链接 HTTP 状态
        goto("/concepts")
        nav = page.evaluate("""() => [...new Set([...document.querySelectorAll('.topbar nav a[href^="/"]')]
            .map(a => a.getAttribute('href')))]""")
        bad_nav = []
        for href in nav:
            r = page.request.get(args.base_url + href)
            if r.status >= 400:
                bad_nav.append((href, r.status))
        check("nav links all 2xx", not bad_nav, str(bad_nav[:5]))

        # 移动端：关键页不横向溢出、无重复 ID
        mobile = browser.new_context(viewport={"width": 390, "height": 844})
        mp = mobile.new_page()
        mp.set_default_timeout(15000)
        # 复用 cookie
        cookies = ctx.cookies()
        mobile.add_cookies(cookies)
        for path, name in [("/concepts", "mobile-concepts"),
                           ("/concepts/new", "mobile-concept-new"),
                           (f"/concepts/{tid}", "mobile-concept-detail"),
                           (f"/graph?root_id={time_interval}&layout=dag", "mobile-graph")]:
            if not tid or not time_interval:
                break
            mp.goto(args.base_url + path, wait_until="networkidle")
            mp.screenshot(path=str(out / f"{name}.png"), full_page=True)
            overflow = mp.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
            check(f"mobile no horizontal overflow {name}", not overflow,
                  "" if not overflow else f"scrollWidth={mp.evaluate('document.documentElement.scrollWidth')}")
            dup = mp.evaluate("""() => {
                const seen = {};
                document.querySelectorAll('[id]').forEach(e => { seen[e.id] = (seen[e.id] || 0) + 1; });
                return Object.entries(seen).filter(([k, v]) => v > 1);
            }""")
            check(f"mobile no duplicate ids {name}", not dup, json.dumps(dup, ensure_ascii=False))
        mobile.close()
        browser.close()

    report = {"base_url": args.base_url, "checks": checks,
              "passed": sum(1 for c in checks if c["ok"]), "total": len(checks)}
    (out / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [c for c in checks if not c["ok"]]
    print(json.dumps({"passed": report["passed"], "total": report["total"]}, ensure_ascii=False))
    for f in failed:
        print(f"  FAIL {f['name']}: {f['detail']}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
