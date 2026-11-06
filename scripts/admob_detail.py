"""One-off AdMob breakdown: by app, ad format, ad unit, country and week.

admob.py fetches account-wide earnings per day for the dashboard. This asks
the same API for the dimensions behind that total, to answer "which app and
which placement is earning". Same four ADMOB_* values from the environment;
run it from the admob-detail workflow, where the secrets live.

Prints markdown tables, and appends them to $GITHUB_STEP_SUMMARY when set.
"""

import os
import sys
from collections import defaultdict
from datetime import date, timedelta

import requests

sys.path.insert(0, os.path.dirname(__file__))
from admob import API, _access_token, _error_message  # noqa: E402

METRICS = ["ESTIMATED_EARNINGS", "IMPRESSIONS", "AD_REQUESTS",
           "MATCHED_REQUESTS", "CLICKS"]


def _d(x):
    return {"year": x.year, "month": x.month, "day": x.day}


def report(pub, token, start, end, dims, metrics=METRICS):
    body = {"reportSpec": {
        "dateRange": {"startDate": _d(start), "endDate": _d(end)},
        "dimensions": dims, "metrics": metrics,
    }}
    r = requests.post(f"{API}/accounts/{pub}/networkReport:generate", json=body,
                      timeout=120, headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        sys.exit(f"report {dims} refused ({r.status_code}) {_error_message(r)[:200]}")
    rows = []
    for chunk in r.json():
        row = chunk.get("row")
        if not row:
            continue
        dv = row.get("dimensionValues", {})
        mv = row.get("metricValues", {})
        out = {}
        for k in dims:
            v = dv.get(k, {})
            out[k] = v.get("displayLabel") or v.get("value") or "?"
        for k in metrics:
            v = mv.get(k, {})
            if "microsValue" in v:
                out[k] = int(v["microsValue"]) / 1e6
            else:
                out[k] = float(v.get("integerValue") or v.get("doubleValue") or 0)
        rows.append(out)
    return rows


def ecpm(r):
    return r["ESTIMATED_EARNINGS"] / r["IMPRESSIONS"] * 1000 if r["IMPRESSIONS"] else 0


def match(r):
    return r["MATCHED_REQUESTS"] / r["AD_REQUESTS"] * 100 if r["AD_REQUESTS"] else 0


def show(r):
    return r["IMPRESSIONS"] / r["MATCHED_REQUESTS"] * 100 if r["MATCHED_REQUESTS"] else 0


def table(title, head, rows):
    lines = [f"\n### {title}\n", "| " + " | ".join(head) + " |",
             "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def perf_rows(rows, keys):
    rows = sorted(rows, key=lambda r: -r["ESTIMATED_EARNINGS"])
    return [[*(r[k] for k in keys), f"${r['ESTIMATED_EARNINGS']:.2f}",
             f"{int(r['IMPRESSIONS']):,}", f"${ecpm(r):.2f}",
             f"{int(r['AD_REQUESTS']):,}", f"{match(r):.0f}%", f"{show(r):.0f}%",
             int(r["CLICKS"])] for r in rows]


PERF = ["Earnings", "Impr.", "eCPM", "Requests", "Match", "Show", "Clicks"]


def main():
    pub = os.environ.get("ADMOB_PUBLISHER_ID")
    token = _access_token()
    if not (pub and token):
        sys.exit("AdMob credentials missing")

    end = date.today() - timedelta(days=1)       # yesterday: today is partial
    start = date(2026, 6, 1)
    last30 = end - timedelta(days=29)
    out = [f"## AdMob detail through {end} (yesterday, Pacific)"]

    # 1. App x month earnings
    rows = report(pub, token, start, end, ["APP", "MONTH"])
    months = sorted({r["MONTH"] for r in rows})
    by = defaultdict(dict)
    for r in rows:
        by[r["APP"]][r["MONTH"]] = r["ESTIMATED_EARNINGS"]
    apps = sorted(by, key=lambda a: -sum(by[a].values()))
    out.append(table("Earnings by app and month",
                     ["App", *months, "Total"],
                     [[a, *(f"${by[a].get(m, 0):.2f}" for m in months),
                       f"${sum(by[a].values()):.2f}"] for a in apps]))

    # 2. App x week, last 10 weeks
    wk_start = end - timedelta(days=69)
    rows = report(pub, token, wk_start, end, ["APP", "WEEK"])
    weeks = sorted({r["WEEK"] for r in rows})
    by = defaultdict(dict)
    for r in rows:
        by[r["APP"]][r["WEEK"]] = r["ESTIMATED_EARNINGS"]
    apps = sorted(by, key=lambda a: -sum(by[a].values()))
    out.append(table("Earnings by app and week (week starting; first/last may be partial)",
                     ["App", *weeks],
                     [[a, *(f"${by[a].get(w, 0):.2f}" for w in weeks)] for a in apps]))

    # 3. Performance, last 30 days
    out.append(table(f"By app, {last30}..{end}", ["App", "Platform", *PERF],
                     perf_rows(report(pub, token, last30, end, ["APP", "PLATFORM"]),
                               ["APP", "PLATFORM"])))
    out.append(table(f"By app and format, {last30}..{end}", ["App", "Format", *PERF],
                     perf_rows(report(pub, token, last30, end, ["APP", "FORMAT"]),
                               ["APP", "FORMAT"])))
    out.append(table(f"By ad unit, {last30}..{end}", ["Ad unit", "Format", *PERF],
                     perf_rows(report(pub, token, last30, end, ["AD_UNIT", "FORMAT"]),
                               ["AD_UNIT", "FORMAT"])))
    rows = report(pub, token, last30, end, ["COUNTRY"])
    out.append(table(f"Top countries, {last30}..{end}", ["Country", *PERF],
                     perf_rows(rows, ["COUNTRY"])[:15]))

    # 4. Account daily, last 30 days, with volume behind the earnings
    rows = sorted(report(pub, token, last30, end, ["DATE"]), key=lambda r: r["DATE"])
    out.append(table("Account by day", ["Date", *PERF],
                     [[r["DATE"], f"${r['ESTIMATED_EARNINGS']:.2f}",
                       f"{int(r['IMPRESSIONS']):,}", f"${ecpm(r):.2f}",
                       f"{int(r['AD_REQUESTS']):,}", f"{match(r):.0f}%",
                       f"{show(r):.0f}%", int(r["CLICKS"])] for r in rows]))

    text = "\n".join(out) + "\n"
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    main()
