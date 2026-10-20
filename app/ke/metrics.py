"""段1移植：KE 标注指标计算与数据校验（与独立版 generate_report.py 同口径）。

输入为行 dict 列表（键：id/domain/summary/status/person/created/started/finished/review/tags），
输出为看板所需的全部数据（含每个自然日的快照），前端零计算。
"""
from __future__ import annotations

import bisect
import math
from collections import Counter
from datetime import date, datetime, time, timedelta

HEADERS = ["题目ID", "领域", "题目摘要", "标注状态", "标注人",
           "创建时间", "开始时间", "完成时间", "复核结论", "问题标签"]
STATUSES = ["待标注", "标注中", "待复核", "已完成", "已退回"]
REWORK_TAG = "返工"       # 返工率近似口径：问题标签含此关键字视为曾返工


def parse_dt(s, warnings, rid, field):
    """解析日期/时间，失败记警告并按空值处理。"""
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    warnings.append(f"{rid}: 字段「{field}」日期无法解析（{s!r}），已按空值处理")
    return None


def normalize(rows, export_day, warnings):
    """状态与时间字段不一致时的处理规则（口径同步展示在看板页脚）。"""
    for r in rows:
        if r["status"] == "已完成" and not r["finished"]:
            # 规则A：状态已完成但无完成时间 → 按导出日补登（保证改状态后曲线同步）
            r["finished"] = datetime.combine(export_day, time(23, 59))
            warnings.append(f"{r['id']}: 状态为已完成但完成时间为空，已按导出日补登")
        elif r["finished"] and r["status"] != "已完成":
            # 规则B：有完成时间但状态非已完成（如完成后被退回）→ 曲线按时间计，状态分布按状态计
            warnings.append(f"{r['id']}: 完成时间非空但状态为「{r['status']}」，"
                            "曲线按完成时间计入、状态分布按状态计")
        if r["finished"] and r["finished"].date() > export_day:
            warnings.append(f"{r['id']}: 完成时间晚于导出日，已按导出日计")
            r["finished"] = datetime.combine(export_day, time(23, 59))


def day_range(a, b):
    out, cur = [], a
    while cur <= b:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def iso(d):
    return d.isoformat() if d else None


def m_d(d):
    return f"{d.month}月{d.day}日"


def build_data(rows, warnings, export_day, deadline):
    """返回看板所需的全部数据（含每个自然日的快照），JS 端零计算。"""
    total = len(rows)
    done_rows = [r for r in rows if r["status"] == "已完成"]
    # 完成时间口径：曲线/燃尽/产能/预计完成日统一按此（含规则A补登、规则B曾完成）
    fin_dates = sorted(r["finished"].date() for r in rows if r["finished"])
    daily_cnt = Counter(fin_dates)
    all_dts = [x for r in rows for x in (r["created"], r["started"], r["finished"]) if x]
    first_day = min((x.date() for x in all_dts), default=export_day)
    created_days = [r["created"].date() for r in rows if r["created"]]
    start_day = min(created_days) if created_days else first_day   # 理想线起点 = 最早创建日
    span = max(1, (deadline - start_day).days)

    # 复核 / 返工 / 问题（导出当日口径）
    reviewed = [r for r in rows if r["review"] in ("一致", "不一致")]
    consistent = sum(1 for r in reviewed if r["review"] == "一致")
    review_rate = consistent / len(reviewed) if reviewed else None
    rework_num = sum(1 for r in rows
                     if r["status"] == "已退回"
                     or (r["status"] == "已完成" and any(REWORK_TAG in t for t in r["tags"])))
    rework_rate = rework_num / len(done_rows) if done_rows else None
    open_probs = [r for r in rows if r["tags"] and r["status"] != "已完成"]
    density = len(open_probs) / len(done_rows) if done_rows else None
    pct = lambda x: "—" if x is None else f"{x:.1%}"

    def snapshot(day):
        """某自然日的完整快照：屏1 全部数字 + 结论 + 告警 + 周报文字。"""
        done = bisect.bisect_right(fin_dates, day)
        remaining = total - done
        rate = done / total if total else 0.0
        week = sum(daily_cnt.get(day - timedelta(days=i), 0) for i in range(7))
        prev = sum(daily_cnt.get(day - timedelta(days=7 + i), 0) for i in range(7))
        avg7 = week / 7
        wow = (week - prev) / prev if prev else None
        if total == 0:
            eta, eta_text, eta_health = None, "暂无数据", "green"
        elif remaining == 0:
            eta, eta_text, eta_health = None, "已全部完成", "green"
        elif avg7 == 0:
            eta, eta_text, eta_health = None, "无法估算", "yellow"
        else:
            need = math.ceil(remaining / avg7)
            if need > 365 * 3:
                eta, eta_text, eta_health = None, "超过 3 年", "red"
            else:
                eta = day + timedelta(days=need)
                eta_text, eta_health = m_d(eta), ("green" if eta <= deadline else "red")
        prog = min(1.0, max(0.0, (day - start_day).days / span))
        ideal_done = total * prog
        lag = ideal_done - done
        lag_pct = lag / total if total else 0.0
        health = "red" if lag_pct > 0.15 else ("yellow" if lag_pct > 0.05 else "green")
        conclusion = f"本周完成 {week} 题，"
        conclusion += (f"较上周 {wow:+.0%}，" if wow is not None else "上周无完成，")
        conclusion += (f"按当前速度 {eta_text} 可完成" if eta else eta_text)
        if health == "green":
            alert = "✓ 进度正常：实际完成与理想线偏差在 5% 以内"
        else:
            sev = "⚠" if health == "yellow" else "⛔"
            extra = "，已超过 15% 警戒线" if health == "red" else ""
            alert = (f"{sev} 进度落后理想线 {lag_pct:.1%}（约 {lag:.0f} 题）{extra}，"
                     "建议加快标注或增援人力")
        risk = {"green": "进度正常。",
                "yellow": "进度略落后于理想线，需关注。",
                "red": f"进度落后理想线{lag_pct:.0%}（约{lag:.0f}题），需立即提速。"}[health]
        wow_txt = f"较上周{wow:+.0%}" if wow is not None else "上周无完成"
        eta_txt = f"预计{eta_text}完成" if eta else eta_text
        report = (f"截至{m_d(day)}，KE标注项目共{total}题，已完成{done}题，完成率{rate:.1%}。"
                  f"本周完成{week}题，{wow_txt}，日均{avg7:.1f}题，按当前速度{eta_txt}"
                  f"（截止{m_d(deadline)}）。未关闭问题{len(open_probs)}个，"
                  f"一致率{pct(review_rate)}，返工率{pct(rework_rate)}（近似口径）。{risk}")
        return {"done": done, "remaining": remaining, "rate": rate,
                "week": week, "prev_week": prev, "wow": wow, "avg7": avg7,
                "eta": iso(eta), "eta_text": eta_text, "eta_health": eta_health,
                "ideal_done": round(ideal_done, 2), "lag": round(lag, 2),
                "lag_pct": round(lag_pct, 4), "health": health,
                "conclusion": conclusion, "alert": alert, "report": report}

    # 时间轴与序列（屏2）
    axis = day_range(first_day, export_day)
    daily = [daily_cnt.get(d, 0) for d in axis]
    cum = [bisect.bisect_right(fin_dates, d) for d in axis]
    burn_end = deadline if deadline >= start_day else export_day
    burn_axis = day_range(start_day, burn_end)
    burn_actual = [(total - bisect.bisect_right(fin_dates, d)) if d <= export_day else None
                   for d in burn_axis]
    burn_ideal = [round(total * (1 - min(1.0, max(0.0, (d - start_day).days / span))), 2)
                  for d in burn_axis]
    snapshots = {iso(d): snapshot(d) for d in axis}

    # 领域（屏3，导出当日状态口径）
    domains = {}
    for r in rows:
        dd = domains.setdefault(r["domain"], {"name": r["domain"], "total": 0,
                                              "counts": {s: 0 for s in STATUSES}, "done": 0})
        dd["total"] += 1
        dd["counts"][r["status"]] += 1
        dd["done"] += r["status"] == "已完成"
    overall_rate = len(done_rows) / total if total else 0.0
    dlist = []
    for dd in domains.values():
        dd["rate"] = dd["done"] / dd["total"] if dd["total"] else 0.0
        ratio = dd["rate"] / overall_rate if overall_rate else 1.0
        dd["health"] = "red" if ratio < 0.6 else ("yellow" if ratio < 0.9 else "green")
        dlist.append(dd)
    dlist.sort(key=lambda x: x["name"])
    rates_sorted = sorted(dlist, key=lambda x: (x["rate"], x["name"]))

    # 人员（屏4）
    persons = {}
    for r in rows:
        pp = persons.setdefault(r["person"], {"name": r["person"], "done": 0,
                                              "hours": [], "returned": 0})
        if r["status"] == "已完成":
            pp["done"] += 1
            if r["started"] and r["finished"]:
                pp["hours"].append((r["finished"] - r["started"]).total_seconds() / 3600)
        if r["status"] == "已退回":
            pp["returned"] += 1
    plist = [{"name": pp["name"], "done": pp["done"],
              "avg_hours": round(sum(pp["hours"]) / len(pp["hours"]), 1) if pp["hours"] else None,
              "rework": (pp["returned"] / pp["done"]) if pp["done"] else (1.0 if pp["returned"] else 0.0)}
             for pp in persons.values()]
    plist.sort(key=lambda x: x["name"])
    all_hours = [h for pp in persons.values() for h in pp["hours"]]
    team = {"n": len(plist), "done": len(done_rows),
            "percap": round(len(done_rows) / len(plist), 1) if plist else 0,
            "avg_hours": round(sum(all_hours) / len(all_hours), 1) if all_hours else None,
            "rework_rate": rework_rate}

    # 问题（屏5）
    tag_counter = Counter(t for r in open_probs for t in r["tags"])
    tag_items = [{"name": k, "count": v} for k, v in tag_counter.most_common(7)]
    rest = sum(v for _, v in tag_counter.most_common()[7:])
    if rest:
        tag_items.append({"name": "其他", "count": rest})
    prob_items = sorted(({"id": r["id"], "summary": r["summary"], "tags": r["tags"],
                          "created": iso(r["created"].date()) if r["created"] else "",
                          "stuck": max(0, (export_day - r["created"].date()).days) if r["created"] else 0}
                         for r in open_probs),
                        key=lambda x: -x["stuck"])
    for i, p in enumerate(prob_items):
        p["top5"] = i < 5

    # 屏2-5 结论（导出当日口径，静态）
    s = snapshots[iso(export_day)] if snapshots else None
    if total == 0 or s is None:
        conclusions = {"s2": "暂无数据", "s3": "暂无数据", "s4": "暂无数据", "s5": "暂无数据"}
    else:
        trend = "上升" if s["week"] > s["prev_week"] else ("下降" if s["week"] < s["prev_week"] else "持平")
        low = rates_sorted[0]
        s3 = (f"「{low['name']}」领域完成率最低（{low['rate']:.0%}），建议优先调配人力"
              if low["rate"] < rates_sorted[-1]["rate"] else "各领域完成率持平")
        s5 = (f"{len(open_probs)} 个未关闭问题，最久已卡 {prob_items[0]['stuck']} 天，"
              f"集中在「{tag_items[0]['name']}」" if open_probs else "当前无未关闭问题")
        conclusions = {
            "s2": f"近 7 日日均完成 {s['avg7']:.1f} 题，较上周{trend}（{s['week']} vs {s['prev_week']}）",
            "s3": s3,
            "s4": (f"团队 {team['n']} 人累计完成 {team['done']} 题（人均 {team['percap']}），"
                   f"平均单题耗时 {team['avg_hours'] if team['avg_hours'] is not None else '—'} 小时，"
                   f"返工率 {pct(rework_rate)}（近似口径）"),
            "s5": s5,
        }

    return {"project": "KE 人工标注项目", "export_day": iso(export_day),
            "first_day": iso(first_day), "deadline": iso(deadline),
            "total": total, "completed": len(done_rows),
            "axis": [iso(d) for d in axis], "daily": daily, "cum": cum,
            "burn_axis": [iso(d) for d in burn_axis],
            "burn_actual": burn_actual, "burn_ideal": burn_ideal,
            "snapshots": snapshots, "domains": dlist, "rates_sorted": rates_sorted,
            "persons": plist, "team": team, "problems": prob_items, "tags": tag_items,
            "review": {"consistent": consistent, "reviewed": len(reviewed), "rate": review_rate},
            "rework": {"num": rework_num, "rate": rework_rate, "denom": len(done_rows)},
            "density": {"open": len(open_probs), "per_done": density},
            "conclusions": conclusions, "warnings": warnings}
