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


def app_detail(app_id):
    versions, rating, reviews, builds = [], None, [], []

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
    for r in data[:5]:
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

    return versions, rating, reviews, builds


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

    today = date.today()
    fetched = 0
    for back in range(1, days + 1):
        day = today - timedelta(days=back)
        key = day.isoformat()
        # Already held, and old enough that Apple will not revise it.
        if back > 3 and all(key in series[a] for a, _ in APPS):
            continue
        counts = downloads_by_app(sales_rows(day))
        if not counts:
            continue
        fetched += 1
        for app_id, _ in APPS:
            series[app_id][key] = counts.get(app_id, 0)

    apps = []
    for app_id, name in APPS:
        versions, rating, reviews, builds = app_detail(app_id)
        apps.append({
            "id": app_id,
            "name": name,
            "url": f"https://apps.apple.com/app/id{app_id}",
            "downloads": dict(sorted(series[app_id].items())),
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
