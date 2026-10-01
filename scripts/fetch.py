"""Pull the portfolio's App Store Connect data into data/portfolio.json.

Runs in GitHub Actions on a schedule and reads its credentials from the
environment, so the signing key never lands in the repository. Running it by
hand falls back to the local key in ~/appstoreconnect, which is how the
history was seeded.

Downloads accumulate: each run merges into whatever is already committed, so
the series eventually reaches further back than the year Apple itself keeps.
"""

import csv
import gzip
import io
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import jwt
import requests

import play

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "portfolio.json"
ASC = "https://api.appstoreconnect.apple.com/v1"

# App ids are public — they appear in every App Store URL.
APPS = [
    ("6773501518", "S-Tier Eats"),
    ("6784340038", "Cosmica: Idle Universe"),
    ("6804512540", "Good Shepherd The Woodlands"),
    ("6785576912", "Woodlands Trail Guide"),
    ("6773332173", "The Woodlands Fishing Guide"),
    ("6796323432", "Houston BBQ Guide"),
    ("6797896315", "Pupduko"),
]


def _creds():
    """Environment first, local files second.

    Actions supplies these as secrets. The fallback exists so the same script
    can seed history from a machine that already holds the key, without the
    key ever entering the repository.
    """
    key = os.environ.get("ASC_PRIVATE_KEY")
    key_id = os.environ.get("ASC_KEY_ID")
    issuer = os.environ.get("ASC_ISSUER_ID")
    vendor = os.environ.get("ASC_VENDOR_NUMBER")

    if not (key and key_id and issuer):
        # Borrowed from the local tooling rather than duplicated here: the
        # key id, issuer and vendor number identify the developer account
        # and have no business being committed to a public repository.
        import sys

        local = Path.home() / "appstoreconnect"
        sys.path.insert(0, str(local))
        import asc_api as _asc
        import downloads as _dl

        key_id = key_id or _asc.KEY_ID
        issuer = issuer or _asc.ISSUER_ID
        vendor = vendor or _dl.VENDOR_NUMBER
        key = key or (local / f"AuthKey_{key_id}.p8").read_text(encoding="utf-8")

    return key, key_id, issuer, vendor


KEY, KEY_ID, ISSUER, VENDOR = _creds()


def token():
    now = int(time.time())
    payload = {"iss": ISSUER, "iat": now, "exp": now + 1200,
               "aud": "appstoreconnect-v1"}
    return jwt.encode(payload, KEY, algorithm="ES256",
                      headers={"kid": KEY_ID, "typ": "JWT"})


def api(path):
    r = requests.get(f"{ASC}/{path}",
                     headers={"Authorization": f"Bearer {token()}"}, timeout=45)
    if r.status_code != 200:
        return {"__error__": r.status_code}
    return r.json()


def sales_rows(day):
    """One day's sales summary. Empty when Apple has not published it yet,
    which is normal for today and often yesterday."""
    r = requests.get(
        f"{ASC}/salesReports",
        headers={"Authorization": f"Bearer {token()}",
                 "Accept": "application/a-gzip"},
        params={
            "filter[frequency]": "DAILY",
            "filter[reportType]": "SALES",
            "filter[reportSubType]": "SUMMARY",
            "filter[reportDate]": day.isoformat(),
            "filter[vendorNumber]": VENDOR,
        },
        timeout=60,
    )
    if r.status_code != 200:
        return []
    try:
        raw = gzip.decompress(r.content).decode("utf-8")
    except OSError:
        raw = r.content.decode("utf-8", "replace")
    return list(csv.DictReader(io.StringIO(raw), delimiter="\t"))


def downloads_by_app(rows):
    """First-time installs per Apple id.

    Product type codes starting with 1 are downloads; updates and
    re-downloads have their own codes and are deliberately excluded — an
    update is not a new person.
    """
    out = {}
    for row in rows:
        ident = (row.get("Apple Identifier") or "").strip()
        ptype = (row.get("Product Type Identifier") or "").strip()
        if not ident or not ptype.startswith("1"):
            continue
        try:
            out[ident] = out.get(ident, 0) + int(row.get("Units") or 0)
        except ValueError:
            pass
    return out


def skus():
    """App SKU -> Apple id, for attributing in-app purchase revenue.

    An IAP row's "Apple Identifier" is the purchase's own id, not the app's,
    and its "Parent Identifier" is the app's *SKU* rather than its numeric id.
    Without this map every paid row keys to an id that is not in APPS and gets
    dropped — which is exactly how this reported 0.00 across the whole
    portfolio while Cosmica was selling bundles daily.
    """
    out = {}
    for app_id, _ in APPS:
        r = api(f"apps/{app_id}")
        if "__error__" in r:
            continue
        sku = (r.get("data", {}).get("attributes", {}) or {}).get("sku")
        if sku:
            out[sku.strip()] = app_id
    return out


def revenue_by_app(rows, sku_map=None):
    """Apple's proceeds per app, split by currency.

    Deliberately not summed across currencies: adding dollars to euros
    produces a confident, wrong number. "Developer Proceeds" is per unit, so
    it is multiplied by Units — and refunds arrive as negative units, which
    nets off correctly without special handling.

    This is the estimate from the daily sales report, not the amount Apple
    actually paid; the payouts live in the monthly financial reports and
    settle differently after adjustments and conversion.

    Returns proceeds and purchase counts, because they answer different
    questions. Units say how many people bought; proceeds say what it was
    worth. Six tip-jar taps and one bundle can total the same money, and only
    the unit count tells them apart — so both are kept rather than one being
    inferred from the other.
    """
    sku_map = sku_map or {}
    out, units = {}, {}
    for row in rows:
        # An in-app purchase names itself in "Apple Identifier" and its app in
        # "Parent Identifier", as a SKU. Resolve to the app so the money lands
        # against the thing that earned it.
        parent = (row.get("Parent Identifier") or "").strip()
        ident = sku_map.get(parent) if parent else (row.get("Apple Identifier") or "").strip()
        if not ident:
            continue
        cur = (row.get("Currency of Proceeds") or "").strip() or "USD"
        try:
            sold = int(row.get("Units") or 0)
            amount = float(row.get("Developer Proceeds") or 0) * sold
        except ValueError:
            continue
        # Only a row with a parent is a purchase. A bare app row is the free
        # download itself, and counting those as purchases would report every
        # install as a sale.
        if parent and sold:
            units[ident] = units.get(ident, 0) + sold
        if amount:
            out.setdefault(ident, {})
            out[ident][cur] = round(out[ident].get(cur, 0.0) + amount, 2)
    return out, units


def app_detail(app_id):
    versions, rating, reviews, builds = [], None, [], []

    # How many in-app purchases the app offers, which is what separates "this
    # earned nothing" from "this cannot earn anything". A revenue row against
    # a church app with no IAPs reads as a fault rather than a fact.
    #
    # Asked of App Store Connect rather than kept in a list here, so an app
    # that gains a tip jar stops being labelled free without anyone editing
    # this file. A failed call gives None, which the page treats as unknown
    # and says nothing either way.
    iap = api(f"apps/{app_id}/inAppPurchasesV2?limit=50")
    iaps = None if "__error__" in iap else len(iap.get("data", []))

    for item in api(f"apps/{app_id}/appStoreVersions?limit=10").get("data", []):
        a = item["attributes"]
        versions.append({
            "version": a.get("versionString"),
            "state": a.get("appStoreState") or a.get("appVersionState"),
            "created": (a.get("createdDate") or "")[:10],
        })

    data = api(f"apps/{app_id}/customerReviews?limit=200&sort=-createdDate").get("data", [])
    scores = [r["attributes"].get("rating") for r in data if r["attributes"].get("rating")]
    if scores:
        rating = {
            "average": round(sum(scores) / len(scores), 2),
            "count": len(scores),
            "histogram": {str(n): scores.count(n) for n in range(1, 6)},
        }
    # Enough for a page that lists them all, rather than the single most
    # recent the cards used to show. Apple hands back 200; these are a few
    # hundred bytes each and the whole portfolio has fewer than a dozen, so
    # the cap exists only to stop a hit app bloating the file indefinitely.
    for r in data[:100]:
        a = r["attributes"]
        reviews.append({
            "rating": a.get("rating"),
            "title": a.get("title"),
            "body": (a.get("body") or "")[:400],
            "nickname": a.get("reviewerNickname"),
            "date": (a.get("createdDate") or "")[:10],
            "territory": a.get("territory"),
        })

    for item in api(f"builds?filter[app]={app_id}&sort=-uploadedDate&limit=5").get("data", []):
        a = item["attributes"]
        builds.append({
            "version": a.get("version"),
            "state": a.get("processingState"),
            "uploaded": (a.get("uploadedDate") or "")[:10],
        })

    return versions, rating, reviews, builds, iaps


def main():
    days = int(os.environ.get("BACKFILL_DAYS", "3"))

    existing = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            existing = {a["id"]: a.get("downloads", {}) for a in prev.get("apps", [])}
        except (ValueError, KeyError):
            existing = {}
    series = {app_id: dict(existing.get(app_id, {})) for app_id, _ in APPS}

    prev_rev = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            prev_rev = {a["id"]: a.get("revenue", {}) for a in prev.get("apps", [])}
        except (ValueError, KeyError):
            prev_rev = {}
    revenue = {
        app_id: {c: dict(d) for c, d in prev_rev.get(app_id, {}).items()}
        for app_id, _ in APPS
    }

    prev_units = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            prev_units = {a["id"]: (a.get("iapUnits") or {}) for a in prev.get("apps", [])}
        except (ValueError, KeyError):
            prev_units = {}
    iap_units = {app_id: dict(prev_units.get(app_id, {})) for app_id, _ in APPS}

    # Play data, merged onto whatever is already committed rather than
    # replacing it. Google keeps a limited window, and an empty result means
    # "could not read the bucket" — usually missing credentials in CI — which
    # must never overwrite a real history with nothing.
    prev_droid = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            prev_droid = {a["id"]: (a.get("android") or {}) for a in prev.get("apps", [])}
        except (ValueError, KeyError):
            prev_droid = {}

    android = {}
    for app_id, _ in APPS:
        held = prev_droid.get(app_id) or {}
        android[app_id] = {
            "installs": dict(held.get("installs") or {}),
            "active": dict(held.get("active") or {}),
        }
    for app_id, entry in play.installs().items():
        if app_id not in android:
            continue
        android[app_id]["installs"].update(entry["installs"])
        android[app_id]["active"].update(entry["active"])

    sku_map = skus()

    today = date.today()
    fetched = 0
    for back in range(1, days + 1):
        day = today - timedelta(days=back)
        key = day.isoformat()
        # Already held, and old enough that Apple will not revise it. FORCE=1
        # overrides, which is needed when a new field is added to the report
        # parsing and the existing days have to be walked again.
        if (
            not os.environ.get("FORCE")
            and back > 3
            and all(key in series[a] for a, _ in APPS)
        ):
            continue
        rows = sales_rows(day)
        counts = downloads_by_app(rows)
        if not counts:
            continue
        fetched += 1
        money, sold = revenue_by_app(rows, sku_map)
        for app_id, _ in APPS:
            series[app_id][key] = counts.get(app_id, 0)
            for cur, amount in money.get(app_id, {}).items():
                revenue[app_id].setdefault(cur, {})[key] = amount
            if sold.get(app_id):
                iap_units[app_id][key] = sold[app_id]

    apps = []
    for app_id, name in APPS:
        versions, rating, reviews, builds, iaps = app_detail(app_id)
        apps.append({
            "id": app_id,
            "name": name,
            "url": f"https://apps.apple.com/app/id{app_id}",
            "downloads": dict(sorted(series[app_id].items())),
            "revenue": {c: dict(sorted(d.items())) for c, d in revenue[app_id].items()},
            "iaps": iaps,
            "iapUnits": dict(sorted(iap_units[app_id].items())),
            # Android lives beside iOS rather than in a parallel app list, and
            # stays absent for apps that are not on Play at all — an empty
            # series would read as "nobody installed it".
            "android": (android[app_id]
                        if android[app_id]["installs"] or android[app_id]["active"]
                        else None),
            "rating": rating,
            "reviews": reviews,
            "versions": versions,
            "builds": builds,
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "apps": apps,
        }, indent=1),
        encoding="utf-8",
    )
    print(f"wrote {OUT} — {len(apps)} apps, "
          f"{sum(len(a['downloads']) for a in apps)} app-days, {fetched} new report(s)")


if __name__ == "__main__":
    main()
