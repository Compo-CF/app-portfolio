"""Apple proceeds per fiscal period, broken down by app and product.

The Payments tab (finance.py) keeps each financial report as one total per
currency, which is right for "what will be deposited" but cannot say which app
or which purchase earned it. This keeps the same report at product level for
the Revenue tab, in its own file (data/revenue.json) so it never contends with
portfolio.json.

Same source, same rules as finance.py:
  * `filter[reportDate]` is Apple's fiscal label: "2026-12" = FY2026 P12
    (30 Aug - 26 Sep 2026), "2027-01" = FY2027 P01 (27 Sep - 31 Oct 2026).
  * Extended Partner Share is already units x share and is net of Apple's
    commission. Each row is in the storefront's currency; Apple converts only
    when it pays. Here each currency is converted at the ECB rate on the
    period's last day (fx.py), the same rate the Payments tab uses, and the
    native amounts are kept alongside so nothing is hidden.
  * A region's sales appear only in the period Apple settles them, so a
    foreign sale can land a period late.
  * The app is not named in the report. It is read from the product id
    (com.centricfiber.<app>.<item>) through APPS below.

Run: python scripts/revenue.py   (Actions runs it after fetch.py)
"""

import gzip
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import jwt
import requests

import fx as fxrates
import play

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "revenue.json"
ASC = "https://api.appstoreconnect.apple.com/v1"

# Product-id segment -> (dashboard app id, display name). An unknown segment is
# shown as itself rather than dropped, so a new app's sales still appear.
APPS = {
    "cosmica": ("6784340038", "Cosmica"),
    "WoodlandsEats": ("6773501518", "S-Tier Eats"),
    "WoodlandsTrailGuide": ("6785576912", "Woodlands Trail Guide"),
    "WoodlandsFishing": ("6773332173", "The Woodlands Fishing Guide"),
    "houbbq": ("6796323432", "Houston BBQ Guide"),
    "pupdoku": ("6797896315", "Pupdoku"),
    "pupduko": ("6797896315", "Pupdoku"),
    "fieldkit": ("6809621921", "Fiber Field Kit"),
    "critterconga": ("6817339425", "Critter Conga"),
    "transmute": ("6821057970", "Alchemica"),
    "alchemica": ("6821057970", "Alchemica"),
    "lowandslow": ("6823629432", "Merge BBQ"),
    "ghostline": ("6830660705", "Ghostline"),
}


def _creds():
    """Environment (Actions secrets) first, the local tooling second."""
    key = os.environ.get("ASC_PRIVATE_KEY")
    key_id = os.environ.get("ASC_KEY_ID")
    issuer = os.environ.get("ASC_ISSUER_ID")
    vendor = os.environ.get("ASC_VENDOR_NUMBER")
    if not (key and key_id and issuer):
        local = Path.home() / "appstoreconnect"
        sys.path.insert(0, str(local))
        import asc_api as _asc
        import downloads as _dl
        key_id = key_id or _asc.KEY_ID
        issuer = issuer or _asc.ISSUER_ID
        vendor = vendor or _dl.VENDOR_NUMBER
        key = key or (local / f"AuthKey_{key_id}.p8").read_text(encoding="utf-8")
    return key, key_id, issuer, vendor


def _token(key, key_id, issuer):
    now = int(time.time())
    return jwt.encode({"iss": issuer, "iat": now, "exp": now + 1200,
                       "aud": "appstoreconnect-v1"},
                      key, algorithm="ES256", headers={"kid": key_id, "typ": "JWT"})


def app_of(sku):
    parts = sku.split(".")
    seg = parts[2] if len(parts) > 3 and parts[0] == "com" else sku
    return APPS.get(seg, (None, seg))


def _iso(s):
    return datetime.strptime(s, "%m/%d/%Y").date().isoformat()


def parse(text, label):
    """One financial report -> {start, end, rows[]} or None.

    Two layouts carry the same numbers. The API returns the aggregated one:
    header on line 0, one row per product x country, the period's own Start
    Date / End Date on every row, SKU under "Vendor Identifier". The detailed
    download from the ASC website (FD_*.txt) has a vendor preamble, one row
    per transaction, and "SKU" / "Sale or Return" / "Transaction Date".
    """
    lines = [l.split("\t") for l in text.splitlines()]
    try:
        hi = next(i for i, c in enumerate(lines)
                  if c and c[0] in ("Transaction Date", "Start Date") and len(c) > 5)
    except StopIteration:
        return None
    hdr = lines[hi]
    meta = {c[0]: c[1] for c in lines[:hi] if len(c) >= 2}
    agg = {}
    first = last = None
    for c in lines[hi + 1:]:
        # Detail rows end at a blank line, "Total_Rows", or the per-country
        # summary table ("Country Of Sale" ...).
        if not c or not c[0] or c[0] in ("Total_Rows", "Country Of Sale"):
            break
        r = dict(zip(hdr, c))
        sku = r.get("SKU") or r.get("Vendor Identifier") or ""
        try:
            units = int(r["Quantity"])
            amount = float(r["Extended Partner Share"])
        except (KeyError, ValueError):
            continue
        if (r.get("Sale or Return") or r.get("Sales or Return")) == "R":
            units = -abs(units)
        app_id, app = app_of(sku)
        k = (app, r["Title"], r["Partner Share Currency"])
        row = agg.setdefault(k, {"appId": app_id, "app": app, "product": r["Title"],
                                 "sku": sku, "currency": k[2],
                                 "units": 0, "amount": 0.0})
        row["units"] += units
        row["amount"] += amount
        lo = _iso(r.get("Transaction Date") or r["Start Date"])
        hi_d = _iso(r.get("Transaction Date") or r["End Date"])
        first = min(first or lo, lo)
        last = max(last or hi_d, hi_d)
    if not agg:
        return None
    start = _iso(meta["Start Date"]) if "Start Date" in meta else first
    end = _iso(meta["End Date"]) if "End Date" in meta else last
    rows = sorted(agg.values(), key=lambda r: (r["app"], r["product"], r["currency"]))
    for r in rows:
        r["amount"] = round(r["amount"], 2)
    return {"label": label, "start": start, "end": end, "rows": rows}


def fetch(label, bearer, vendor):
    try:
        r = requests.get(f"{ASC}/financeReports", timeout=60,
                         headers={"Authorization": f"Bearer {bearer}"},
                         params={"filter[regionCode]": "ZZ",
                                 "filter[reportDate]": label,
                                 "filter[reportType]": "FINANCIAL",
                                 "filter[vendorNumber]": vendor})
    except requests.RequestException as e:
        print(f"  revenue {label}: unreachable ({type(e).__name__})")
        return None
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        print(f"  revenue {label}: refused ({r.status_code})")
        return None
    try:
        text = gzip.decompress(r.content).decode("utf-8", "replace")
    except (OSError, EOFError):
        text = r.content.decode("utf-8", "replace")
    return parse(text, label)


# How far back the open window may reach. A financial report later than this
# is its own problem; the window should not grow without bound meanwhile.
OPEN_MAX_DAYS = 45


def sales_day(day, bearer, vendor):
    """One day's in-app sales by purchase, from the DAILY sales summary.

    None when Apple has not published that day (a 404, normal for yesterday),
    so the day is asked about again next run; [] when it has and nothing was
    bought. Read with the rules in reference_asc_sales_report_parsing:
    Developer Proceeds is per unit, refunds are negative units, and each row
    is in the currency it was earned in.
    """
    try:
        r = requests.get(f"{ASC}/salesReports", timeout=60,
                         headers={"Authorization": f"Bearer {bearer}",
                                  "Accept": "application/a-gzip"},
                         params={"filter[frequency]": "DAILY",
                                 "filter[reportType]": "SALES",
                                 "filter[reportSubType]": "SUMMARY",
                                 "filter[reportDate]": day,
                                 "filter[vendorNumber]": vendor})
    except requests.RequestException as e:
        print(f"  sales {day}: unreachable ({type(e).__name__})")
        return None
    if r.status_code != 200:
        if r.status_code != 404:
            print(f"  sales {day}: refused ({r.status_code})")
        return None
    try:
        text = gzip.decompress(r.content).decode("utf-8", "replace")
    except (OSError, EOFError):
        text = r.content.decode("utf-8", "replace")

    lines = text.splitlines()
    if not lines:
        return []
    hdr = lines[0].split("\t")
    agg = {}
    for line in lines[1:]:
        row = dict(zip(hdr, line.split("\t")))
        ptype = (row.get("Product Type Identifier") or "").strip()
        try:
            units = int(row.get("Units") or 0)
            per = float(row.get("Developer Proceeds") or 0)
        except ValueError:
            continue
        # In-app purchases are the IA* types. A paid app's own sale would be
        # a non-IA row with proceeds, so keep any row that earned something.
        if not (ptype.startswith("IA") or per):
            continue
        sku = (row.get("SKU") or "").strip()
        cur = (row.get("Currency of Proceeds") or "USD").strip()
        app_id, app = app_of(sku)
        k = (app, row.get("Title") or sku, cur)
        out = agg.setdefault(k, {"appId": app_id, "app": app, "product": k[1],
                                 "sku": sku, "currency": cur,
                                 "units": 0, "amount": 0.0})
        out["units"] += units
        out["amount"] += units * per
    rows = sorted(agg.values(), key=lambda r: (r["app"], r["product"], r["currency"]))
    for r in rows:
        r["amount"] = round(r["amount"], 2)
    return [r for r in rows if r["units"] or r["amount"]]


def open_sales(periods, prev_open, bearer, vendor, today):
    """Sales by purchase for the days no financial report covers yet.

    The window starts the day after the newest settled period and runs to
    yesterday. A published day's report does not change, so held days are
    never refetched; when the next financial report closes, the days it
    covers fall out of the window and are dropped.
    """
    if not periods:
        return None
    start = date.fromisoformat(periods[-1]["end"]) + timedelta(days=1)
    held = {d: rows for d, rows in ((prev_open or {}).get("days") or {}).items()
            if d >= start.isoformat()}
    last = today - timedelta(days=1)
    first = max(start, last - timedelta(days=OPEN_MAX_DAYS))
    fetched = 0
    day = first
    while day <= last:
        iso = day.isoformat()
        if iso not in held:
            rows = sales_day(iso, bearer, vendor)
            if rows is not None:
                held[iso] = rows
                fetched += 1
        day += timedelta(days=1)
    if fetched:
        print(f"  sales: {fetched} day(s) fetched for the open period")
    if not held:
        return {"start": start.isoformat(), "through": None, "missing": [], "days": {}}
    through = max(held)
    # Days inside the window Apple has not answered for. Usually none; listed
    # so the page can say the total is short rather than quietly being short.
    missing, d = [], first
    while d.isoformat() < through:
        if d.isoformat() not in held:
            missing.append(d.isoformat())
        d += timedelta(days=1)
    return {"start": start.isoformat(), "through": through, "missing": missing,
            "days": dict(sorted(held.items()))}


def _order(label):
    y, p = label.split("-")
    return int(y), int(p)


def candidates(held, today):
    """Labels worth asking about: everything after the newest held period
    that could have closed by now, or a two-year sweep the first time."""
    labels = [f"{y}-{p:02d}" for y in (today.year, today.year + 1) for p in range(1, 13)]
    if not held:
        return labels
    newest = max(held, key=_order)
    newest_end = date.fromisoformat(held[newest]["end"])
    ahead = max(1, (today - newest_end).days // 28 + 2)
    return [l for l in labels if _order(l) > _order(newest)][:ahead]


def main():
    prev = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
        except ValueError:
            prev = {}
    held = {p["label"]: p for p in prev.get("periods", [])}

    key, key_id, issuer, vendor = _creds()
    bearer = _token(key, key_id, issuer)
    found = 0
    for label in candidates(held, date.today()):
        p = fetch(label, bearer, vendor)
        if p:
            held[label] = p
            found += 1

    periods = sorted(held.values(), key=lambda p: p["end"])
    open_ = open_sales(periods, prev.get("open"), bearer, vendor, date.today())

    # Settled periods convert at their last day's rate (as the Payments tab
    # does); open days at each day's own rate (as the estimate does).
    wanted = {}
    for p in periods:
        for r in p["rows"]:
            if r["currency"] != "USD":
                wanted.setdefault(p["end"], set()).add(r["currency"])
    for d, rows in ((open_ or {}).get("days") or {}).items():
        for r in rows:
            if r["currency"] != "USD":
                wanted.setdefault(d, set()).add(r["currency"])
    rates = fxrates.rates(wanted, prev.get("fx") or {})

    def convert(rows, on):
        day = rates.get(on) or {}
        for r in rows:
            rate = 1.0 if r["currency"] == "USD" else day.get(r["currency"])
            r["usd"] = round(r["amount"] * rate, 2) if rate else None

    for p in periods:
        convert(p["rows"], p["end"])
    for d, rows in ((open_ or {}).get("days") or {}).items():
        convert(rows, d)

    # Google Play, kept apart from Apple's periods rather than merged into
    # them. Play settles by calendar month and Apple by fiscal period, so a
    # shared column would put Play's October against Apple's 27 Sep - 31 Oct
    # and quietly misattribute both. Its figures are already net of Google's
    # fee and already in the merchant currency.
    #
    # Optional: a failure here must not cost the Apple breakdown, which is
    # what this file is mainly for. Keep whatever was held instead.
    try:
        play_rev = play.earnings() or {}
    except Exception as e:                           # noqa: BLE001
        print(f"revenue: Play earnings unavailable ({type(e).__name__})")
        play_rev = {}
    # Play reports a package, not a display name. APPS already maps the
    # dashboard's app ids to names for Apple, so invert it rather than keep a
    # second list that can drift out of step with the first.
    names = {app_id: name for app_id, name in APPS.values()}
    play_items = []
    for app_id, items in (play_rev.get("products") or {}).items():
        for item in items.values():
            play_items.append({**item, "appId": app_id,
                               "app": names.get(app_id, app_id)})
    play_items.sort(key=lambda r: -sum(
        v for d in r["currency"].values() for v in d.values()))
    play_block = ({"through": play_rev.get("through"), "products": play_items}
                  if play_items else prev.get("play"))

    out = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "fx": {d: v for d, v in rates.items() if d in wanted},
           "periods": periods,
           "open": open_,
           "play": play_block}
    if prev and {k: v for k, v in out.items() if k != "generated"} == \
            {k: v for k, v in prev.items() if k != "generated"}:
        print(f"revenue: nothing new ({len(periods)} periods)")
        return
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT.name}: {len(periods)} periods, {found} fetched this run")


if __name__ == "__main__":
    main()
