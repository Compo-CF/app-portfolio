"""AdMob readiness per app: approval state, store link, ad units, last-7-day fill.

Answers "is AdMob ready on everything?". An app serves limited or no ads until
AdMob approves it, and approval needs the app linked to its store listing and
app-ads.txt verified. Run from the admob-detail workflow (secrets live there).
"""

import os
import sys
from collections import defaultdict
from datetime import date, timedelta

import requests

sys.path.insert(0, os.path.dirname(__file__))
from admob import _access_token, _error_message  # noqa: E402
from admob_detail import report, table  # noqa: E402

BETA = "https://admob.googleapis.com/v1beta"


def _list(pub, token, kind):
    out, page = [], None
    while True:
        params = {"pageSize": 1000}
        if page:
            params["pageToken"] = page
        r = requests.get(f"{BETA}/accounts/{pub}/{kind}", params=params, timeout=60,
                         headers={"Authorization": f"Bearer {token}"})
        if r.status_code != 200:
            sys.exit(f"{kind} list refused ({r.status_code}) {_error_message(r)[:200]}")
        body = r.json()
        out += body.get(kind, [])
        page = body.get("nextPageToken")
        if not page:
            return out


def main():
    pub = os.environ.get("ADMOB_PUBLISHER_ID")
    token = _access_token()
    if not (pub and token):
        sys.exit("AdMob credentials missing")

    apps = _list(pub, token, "apps")
    units = _list(pub, token, "adUnits")
    per_app = defaultdict(list)
    for u in units:
        per_app[u.get("appId")].append(
            f"{u.get('displayName')} ({(u.get('adFormat') or '?').lower()})")

    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=6)
    fill = {}
    # Same display name on iOS and Android, so key by name + platform.
    for r in report(pub, token, start, end, ["APP", "PLATFORM"]):
        fill[(r["APP"], r["PLATFORM"].upper())] = r

    rows = []
    for a in apps:
        linked = a.get("linkedAppInfo") or {}
        name = linked.get("displayName") or (a.get("manualAppInfo") or {}).get("displayName") or "?"
        state = a.get("appApprovalState") or "UNKNOWN"
        store = linked.get("appStoreId") or "NOT LINKED"
        f = fill.get((name, (a.get("platform") or "").upper()))
        req = int(f["AD_REQUESTS"]) if f else 0
        m = f"{f['MATCHED_REQUESTS'] / f['AD_REQUESTS'] * 100:.0f}%" if f and f["AD_REQUESTS"] else "-"
        imp = int(f["IMPRESSIONS"]) if f else 0
        rows.append([name, a.get("platform", "?"), state, store, len(per_app[a.get("appId")]),
                     f"{req:,}", m, f"{imp:,}",
                     "; ".join(sorted(per_app[a.get("appId")])) or "none"])
    rows.sort(key=lambda r: (r[2] == "APPROVED", r[0]))

    text = table(f"AdMob apps: approval, store link, fill {start}..{end}",
                 ["App", "Platform", "Approval", "Store ID", "Units", "Requests 7d",
                  "Match", "Impr. 7d", "Ad units"], rows) + "\n"
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)


if __name__ == "__main__":
    main()
