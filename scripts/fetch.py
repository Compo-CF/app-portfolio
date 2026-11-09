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

import admob
import analytics
import finance
import fx as fxrates
import play
import storeratings

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "portfolio.json"
ASC = "https://api.appstoreconnect.apple.com/v1"

# App ids are public — they appear in every App Store URL.
#
# This is every app on the account, checked against it on 2026-11-03 rather
# than assumed. Two ids this list used to call deliberate omissions —
# Pit Clock (6811207479) and Chirp Meter Dashboard (6776745788) — no longer
# resolve, so there is nothing left to omit and the note saying otherwise has
# gone with them.
#
# An app is added as soon as it exists, not when it goes live. The download
# series only accumulates from the day an app appears here, so adding one
# while it is still in review captures launch day instead of backfilling it
# later from whatever Apple still holds.
APPS = [
    ("6773501518", "S-Tier Eats"),
    ("6784340038", "Cosmica: Idle Universe"),
    ("6804512540", "Good Shepherd The Woodlands"),
    ("6785576912", "Woodlands Trail Guide"),
    ("6773332173", "The Woodlands Fishing Guide"),
    ("6796323432", "Houston BBQ Guide"),
    ("6797896315", "Pupduko"),
    ("6809621921", "Fiber Field Kit"),
    ("6817339425", "Critter Conga"),
    ("6821057970", "Alchemica"),
    # Each of these was in review when added, listed then for the reason above.
    ("6823629432", "Merge BBQ: Low & Slow"),
    ("6830660705", "Ghostline: Idle Racer"),
    ("6832097718", "Neatly: Cozy Organizing"),
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


def api(path, attempts=3):
    """One App Store Connect call, retried when Apple is merely slow.

    Apple times out often enough that, unhandled, it is the usual way this
    job dies: one read timeout anywhere in the run raised out of main() and
    nothing was committed at all — two runs in a row on 2026-11-03, in
    different calls each time. The portfolio makes dozens of these per run
    and grew two more apps that day, so the odds only go up.

    Retries cover timeouts and Apple's own 5xx and 429. A 4xx is an answer,
    not a stumble, and is handed back as it always was.
    """
    for attempt in range(attempts):
        try:
            r = requests.get(f"{ASC}/{path}",
                             headers={"Authorization": f"Bearer {token()}"},
                             timeout=45)
        except requests.RequestException as e:
            if attempt == attempts - 1:
                print(f"  asc: {path} unreachable after {attempts} tries "
                      f"({type(e).__name__})")
                return {"__error__": "timeout"}
            time.sleep(2 * (attempt + 1))
            continue

        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
            time.sleep(2 * (attempt + 1))
            continue
        return {"__error__": r.status_code}


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


def downloads_by_country(rows):
    """First-time installs per app, split by the storefront they came from.

    Returned for the one day of rows handed in, and stored keyed by that
    date. Keying by date rather than keeping a running total is what makes a
    re-fetch safe: re-reading a day overwrites it, where adding to a total
    would count it twice and silently double the map.

    Sparse on purpose — a day with no installs for an app records nothing, so
    this stays small despite being per-day.

    Same product-type rule as downloads_by_app — codes starting with 1 are
    first installs, and updates and re-downloads are somebody who already
    had it.
    """
    out = {}
    for row in rows:
        ident = (row.get("Apple Identifier") or "").strip()
        ptype = (row.get("Product Type Identifier") or "").strip()
        country = (row.get("Country Code") or "").strip().upper()
        if not ident or not country or not ptype.startswith("1"):
            continue
        try:
            units = int(row.get("Units") or 0)
        except ValueError:
            continue
        if units:
            out.setdefault(ident, {})
            out[ident][country] = out[ident].get(country, 0) + units
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
    versions, review_rating, reviews, builds = [], None, [], []

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

    # Written reviews only. This used to be the app's headline "rating", which
    # understated every app: most people tap stars and write nothing, and
    # those never appear here (Cosmica on 2026-11-05: 3.4 from 5 here against
    # 4.07 from 14 on the US store). The headline now comes from
    # storeratings.py; this stays as reviewRating, the average and histogram
    # of what people bothered to write about.
    data = api(f"apps/{app_id}/customerReviews?limit=200&sort=-createdDate").get("data", [])
    scores = [r["attributes"].get("rating") for r in data if r["attributes"].get("rating")]
    if scores:
        review_rating = {
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

    for i, item in enumerate(api(f"builds?filter[app]={app_id}"
                                 "&sort=-uploadedDate&limit=5").get("data", [])):
        a = item["attributes"]
        entry = {
            "version": a.get("version"),           # build number, not 2.6
            "state": a.get("processingState"),
            "uploaded": (a.get("uploadedDate") or "")[:10],
        }
        # Only for the newest, which is the one that can be stranded — two
        # extra calls per app rather than ten.
        #
        # "version" here is CFBundleVersion: 90, or 20260930.639. Comparing it
        # against a versionString like 2.6 is meaningless, so ask instead
        # whether the build is attached to a version record at all. An
        # unattached build processes cleanly, appears in TestFlight, and can
        # never reach review — which is exactly the failure worth catching.
        if i == 0:
            pre = api(f"builds/{item['id']}/preReleaseVersion").get("data")
            att = api(f"builds/{item['id']}/appStoreVersion").get("data")
            entry["marketing"] = pre["attributes"].get("version") if pre else None
            entry["attached"] = att["attributes"].get("versionString") if att else None
        builds.append(entry)

    return versions, review_rating, reviews, builds, iaps


def main():
    days = int(os.environ.get("BACKFILL_DAYS", "3"))

    # Sales-report downloads (Pacific-time days). Stored as downloadsSales
    # since the switch to App Analytics; before it, "downloads" held exactly
    # this series, so that is the fallback for the first run after the switch.
    existing = {}
    prev_utc, prev_meta = {}, {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            existing = {a["id"]: (a.get("downloadsSales")
                                  if "downloadsSales" in a else a.get("downloads", {}))
                        for a in prev.get("apps", [])}
            prev_utc = {a["id"]: (a.get("downloadsUtc") or {}) for a in prev.get("apps", [])}
            prev_meta = {a["id"]: (a.get("analytics") or {}) for a in prev.get("apps", [])}
        except (ValueError, KeyError):
            existing, prev_utc, prev_meta = {}, {}, {}
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

    # Country totals accumulate the same way downloads do, so the picture
    # reaches further back than the window Apple still serves.
    prev_cc = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            prev_cc = {a["id"]: (a.get("countries") or {}) for a in prev.get("apps", [])}
        except (ValueError, KeyError):
            prev_cc = {}
    countries = {app_id: {d: dict(v) for d, v in prev_cc.get(app_id, {}).items()}
                 for app_id, _ in APPS}

    # A daily snapshot of the rating, because Apple only ever reports the
    # current one. Without keeping it, "is this recovering?" is unanswerable
    # — a 2-star review drops the average visibly and nothing records when,
    # or whether it came back.
    #
    # Two series, never mixed. storeRatingHistory is the store's own star
    # rating (all ratings, from the iTunes lookup) and starts on 2026-11-05.
    # reviewRatingHistory is the written-review average; until that date it
    # was stored as "ratingHistory" and passed off as the rating, so it is
    # carried over under its honest name rather than spliced onto the store
    # series, where the switch would have drawn as a fake jump (3.4 to 4.3).
    prev_rev_hist, prev_store_hist, prev_store = {}, {}, {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            for a in prev.get("apps", []):
                prev_rev_hist[a["id"]] = (a.get("reviewRatingHistory")
                                          or a.get("ratingHistory") or {})
                prev_store_hist[a["id"]] = a.get("storeRatingHistory") or {}
                # Only a lookup-sourced rating is a usable fallback; the old
                # written-review one would put the wrong numbers back.
                r = a.get("rating") or {}
                if r.get("source") == "itunes-lookup":
                    prev_store[a["id"]] = r
        except (ValueError, KeyError):
            prev_rev_hist, prev_store_hist, prev_store = {}, {}, {}
    review_history = {app_id: dict(prev_rev_hist.get(app_id, {})) for app_id, _ in APPS}
    store_history = {app_id: dict(prev_store_hist.get(app_id, {})) for app_id, _ in APPS}

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
            "revenue": {c: dict(d) for c, d in (held.get("revenue") or {}).items()},
        }
    for app_id, entry in play.installs().items():
        if app_id not in android:
            continue
        android[app_id]["installs"].update(entry["installs"])
        android[app_id]["active"].update(entry["active"])

    # Play money, kept per currency per day exactly as Apple's is, because
    # Google settles in the merchant currency and one app can earn in more
    # than one. Merged onto what is held rather than replacing it, so a run
    # that cannot reach the bucket leaves real history alone instead of
    # zeroing it - the same rule the install series follows above.
    play_money = play.earnings() or {}
    for app_id, by_cur in (play_money.get("apps") or {}).items():
        if app_id not in android:
            continue
        held_rev = android[app_id].setdefault("revenue", {})
        # Deliberately not `cur, days`: main() is long, and `days` is the
        # backfill window set from BACKFILL_DAYS several hundred lines up.
        # Rebinding it here turned an int into a dict and only blew up much
        # later, in code that had nothing to do with Play.
        for money_cur, day_rows in by_cur.items():
            held_rev.setdefault(money_cur, {}).update(day_rows)

    # How long since Google last gave us a day it had not given us before.
    #
    # Play's lag is not the thing worth watching. It sits around four days,
    # and that is simply how long Google takes to finalise device counts.
    # What is worth watching is the lag *not moving*: through late September
    # the coverage date froze at 20 September for six consecutive fetches,
    # then jumped five days at once. Nothing errors when that happens —
    # play.py reads the file, the file just holds nothing new — so the page
    # went on looking healthy while Android was a fortnight stale.
    #
    # `through` is the newest day any app has; `advanced` is the fetch on
    # which `through` last increased. Today minus `advanced` is the stall.
    play_through = max(
        (d for a in android.values() for d in (a["installs"] or {})), default=None)
    prev_play = {}
    if OUT.exists():
        try:
            prev_play = dict(
                json.loads(OUT.read_text(encoding="utf-8")).get("play") or {})
        except (ValueError, KeyError):
            prev_play = {}
    if play_through:
        moved = play_through > (prev_play.get("through") or "")
        play_feed = {
            "through": play_through,
            # Earnings run on their own clock: monthly and in arrears, where
            # installs are daily and about four days behind. One coverage
            # date cannot stand for both, and conflating them would let an
            # unpublished month read as a month that earned nothing.
            "revenueThrough": (play_money.get("through")
                               or (prev_play or {}).get("revenueThrough")),
            # The first run after this was added cannot know when the date
            # really last moved, so it claims today and reads as healthy. It
            # corrects itself the first time Google actually publishes.
            "advanced": (date.today().isoformat()
                         if moved or not prev_play.get("advanced")
                         else prev_play["advanced"]),
        }
    else:
        # No Android data at all. Keep what was known rather than invent a
        # stall out of a run that simply had no credentials.
        play_feed = prev_play or None

    # Ad income, kept at the top level rather than per app: AdMob names apps
    # its own way and a guessed mapping would credit one app's earnings to
    # another. Merged onto what is committed, so a run without credentials
    # leaves the history alone instead of erasing it.
    prev_ads = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            prev_ads = dict(prev.get("admob") or {})
        except (ValueError, KeyError):
            prev_ads = {}
    ads = dict(prev_ads)
    ads.update(admob.earnings())

    # What Apple actually owes, period by period, from the financial reports
    # rather than the daily estimate. Merged onto what is committed for the
    # same reason as the ads above, and because the probe bounds itself by
    # what is already held: discard it and every run sweeps two fiscal years.
    prev_payouts = []
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            prev_payouts = list(prev.get("payouts") or [])
        except (ValueError, KeyError):
            prev_payouts = []
    payouts = finance.periods(token(), VENDOR, known=prev_payouts)

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
        by_country = downloads_by_country(rows)
        for app_id, _ in APPS:
            series[app_id][key] = counts.get(app_id, 0)
            for cur, amount in money.get(app_id, {}).items():
                revenue[app_id].setdefault(cur, {})[key] = amount
            if sold.get(app_id):
                iap_units[app_id][key] = sold[app_id]
            if by_country.get(app_id):
                countries[app_id][key] = by_country[app_id]

    # App Analytics first-time downloads on UTC days — the same numbers the
    # App Analytics pages show. The first run asks Apple to start producing
    # the reports (1-2 days before any arrive) plus a one-time snapshot for
    # history; later runs only re-read the last ~10 days of instances, since
    # Apple restates recent days as late events arrive.
    client = analytics.Client(token)
    utc = {app_id: dict(prev_utc.get(app_id, {})) for app_id, _ in APPS}
    meta = {app_id: dict(prev_meta.get(app_id, {})) for app_id, _ in APPS}
    force = bool(os.environ.get("FORCE"))
    for app_id, _ in APPS:
        try:
            ongoing, snapshot = analytics.ensure_requests(client, app_id)
            if force or not meta[app_id].get("history"):
                got = analytics.first_time_downloads(client, app_id, snapshot + ongoing)
                if got:
                    meta[app_id]["history"] = True
            else:
                since = (today - timedelta(days=10)).isoformat()
                got = analytics.first_time_downloads(client, app_id, ongoing, since=since)
            utc[app_id].update(got)
        except requests.RequestException as e:
            # Network trouble here must not cost the rest of the refresh.
            print(f"  analytics: {app_id} skipped this run ({e})")

    # Storefronts worth asking: every country any app has ever been
    # downloaded in (nobody can rate an app they never installed), the US
    # always, and anywhere a rating was found before in case the download
    # series has a gap. One call per storefront covers every app.
    storefronts = {"US"}
    for app_id, _ in APPS:
        for day in countries[app_id].values():
            storefronts.update(day)
        storefronts.update((prev_store.get(app_id) or {}).get("storefronts") or {})
    store_rating, failed = storeratings.fetch(
        [a for a, _ in APPS], storefronts, prev_store)
    print(f"  ratings: {len(storefronts)} storefront(s) asked"
          + (f", kept last run's figures for {', '.join(failed)}" if failed else ""))

    apps = []
    for app_id, name in APPS:
        versions, review_rating, reviews, builds, iaps = app_detail(app_id)
        # A storefront that failed this run already carries last run's
        # figures (storeratings.fetch), so this is never a fake drop.
        rating = store_rating.get(app_id)

        # The page draws "downloads". It switches to UTC days as soon as Apple
        # has delivered App Analytics data for the app, and stays on the
        # Pacific-time sales series until then so no card goes blank while the
        # first reports are being generated.
        #
        # Two guards keep the switch honest:
        #  - only once the UTC series reaches within 3 days of the sales
        #    series. A one-time snapshot stops on the day it was taken, and
        #    switching on stale data would show the last week as missing.
        #  - days inside the UTC range that Apple hasn't delivered (the stretch
        #    between an old snapshot and the first ongoing report) are filled
        #    from the sales series and listed, rather than drawn as zero.
        sales_dl, utc_dl = series[app_id], utc[app_id]
        newest_sales = max(sales_dl) if sales_dl else None
        on_utc = bool(utc_dl) and (
            not newest_sales
            or max(utc_dl) >= (date.fromisoformat(newest_sales) - timedelta(days=3)).isoformat())
        gap_days = []
        if on_utc:
            shown = dict(utc_dl)
            lo, hi = min(utc_dl), max(utc_dl)
            # App Analytics omits days with no downloads, so a missing day is
            # usually a real zero; the sales series (which has every day,
            # zeros included) fills them. Only days where sales saw downloads
            # are listed as "not yet delivered" — listing every quiet day
            # would bury the few that matter.
            for d in sales_dl:
                if lo <= d <= hi and d not in utc_dl:
                    shown[d] = sales_dl[d]
                    if sales_dl[d]:
                        gap_days.append(d)
            gap_days.sort()
        else:
            shown = sales_dl
        newest = max(shown) if shown else None

        # Keyed by the day it was observed, so re-running overwrites rather
        # than appending a second reading for the same date.
        # Not on a run where no storefront answered: that would log a copy of
        # yesterday as if it had been observed today.
        if rating and len(failed) < len(storefronts):
            us = rating["storefronts"].get("US")
            store_history[app_id][today.isoformat()] = {
                "avg": rating["average"], "count": rating["count"],
                "us": {"avg": us["average"], "count": us["count"]} if us else None}
        if review_rating:
            review_history[app_id][today.isoformat()] = {
                "avg": review_rating["average"], "count": review_rating["count"]}
        apps.append({
            "id": app_id,
            "name": name,
            "url": f"https://apps.apple.com/app/id{app_id}",
            "downloads": dict(sorted(shown.items())),
            # "utc" = App Analytics (matches the App Analytics pages);
            # "pacific" = daily sales report, used until Analytics data lands.
            "downloadsBasis": "utc" if on_utc else "pacific",
            # Apple settles App Analytics days within about two days; the
            # page marks these as still filling in.
            "downloadsProvisional": ([d for d in sorted(shown)
                                      if newest and d > (date.fromisoformat(newest)
                                                         - timedelta(days=2)).isoformat()]
                                     if on_utc else []),
            # Days in the UTC range filled from the sales report (Pacific days).
            "downloadsGapsFromSales": gap_days,
            "downloadsUtc": dict(sorted(utc[app_id].items())),
            "downloadsSales": dict(sorted(series[app_id].items())),
            "analytics": meta[app_id],
            "revenue": {c: dict(sorted(d.items())) for c, d in revenue[app_id].items()},
            "iaps": iaps,
            "iapUnits": dict(sorted(iap_units[app_id].items())),
            "countries": dict(sorted(countries[app_id].items())),
            # Android lives beside iOS rather than in a parallel app list, and
            # stays absent for apps that are not on Play at all — an empty
            # series would read as "nobody installed it".
            "android": (android[app_id]
                        if android[app_id]["installs"] or android[app_id]["active"]
                        else None),
            # The store's star rating, all ratings across storefronts
            # (storeratings.py). average/count keep their old names so
            # anything summing them gets the right numbers.
            "rating": rating,
            "storeRatingHistory": dict(sorted(store_history[app_id].items())),
            # Written reviews only, with the star histogram of those reviews.
            # Not the app's rating; see app_detail().
            "reviewRating": review_rating,
            "reviewRatingHistory": dict(sorted(review_history[app_id].items())),
            "reviews": reviews,
            "versions": versions,
            "builds": builds,
        })

    # Exchange rates for every day that carries foreign proceeds, so the page
    # can show one dollar figure instead of a USD figure plus four currencies
    # it leaves the reader to add up. Merged onto what is committed: a past
    # day's ECB rate does not change, and a run that cannot reach the rate
    # service must leave the converted history alone rather than blank it.
    prev_fx = {}
    if OUT.exists():
        try:
            prev_fx = dict(json.loads(OUT.read_text(encoding="utf-8")).get("fx") or {})
        except (ValueError, KeyError):
            prev_fx = {}
    # The finance reports settle each region in its own currency (EUR, GBP,
    # KRW...), not one USD sum, so the payout currencies need a rate too: the
    # ECB rate on the period's last day, borrowed from the prior working day
    # when that is a weekend.
    wanted = fxrates.needed(apps)
    for p in payouts:
        if p.get("currency") and p["currency"] != "USD" and p.get("amount"):
            wanted.setdefault(p["end"], set()).add(p["currency"])
    rates = fxrates.rates(wanted, prev_fx)

    # What the conversion is worth against what Apple actually settled.
    #
    # The converted figures use ECB reference rates; Apple uses its own, on
    # its own date. Rather than claim the two agree, each closed period is
    # measured against the finance report for it — every currency in it, each
    # converted at the period-end ECB rate. (Until 2026-11-05 only the USD row
    # was compared, which made FY2027 P01 read $343.70 settled against a $471
    # estimate when Apple had in fact settled about $490 across eight
    # currencies.) The windows also do not line up exactly: the finance period
    # runs a day or two behind the sales-report days (FY2026 P12 closed early
    # at the fiscal year end and its last two days settled in FY2027 P01), so
    # a single period can be off by a day or two of sales. The page adds a
    # running total, across which those boundary shifts cancel.
    by_period = {}
    for p in payouts:
        by_period.setdefault(p["period"], []).append(p)
    settled = []
    for label in sorted(by_period, key=lambda l: by_period[l][0]["end"]):
        rows = by_period[label]
        start, end = rows[0]["start"], rows[0]["end"]
        currencies, unconverted, apple = {}, {}, 0.0
        for p in rows:
            cur = p.get("currency") or "USD"
            amount = p.get("amount") or 0.0
            currencies[cur] = round(currencies.get(cur, 0.0) + amount, 2)
            if cur == "USD":
                apple += amount
                continue
            rate = (rates.get(end) or {}).get(cur)
            if rate is None:
                # Said out loud rather than dropped: a total that quietly
                # omits a currency is the error this block exists to fix.
                unconverted[cur] = round(unconverted.get(cur, 0.0) + amount, 2)
                continue
            apple += amount * rate
        window = 0.0
        for app in apps:
            converted, _ = fxrates.in_usd(app.get("revenue"), rates)
            window += sum(v for d, v in converted.items() if start <= d <= end)
        settled.append({
            "period": label,
            "start": start,
            "end": end,
            "apple": round(apple, 2),
            "appleUsd": currencies.get("USD", 0.0),
            "currencies": currencies,
            "unconverted": unconverted,
            "estimated": round(window, 2),
        })
        if unconverted:
            print(f"  settled {label}: no ECB rate for {sorted(unconverted)}; "
                  "left out of the converted total")

    missed = {}
    for app in apps:
        _, gaps = fxrates.in_usd(app.get("revenue"), rates)
        for day, curs in gaps.items():
            missed.setdefault(day, set()).update(curs)
    if missed:
        print(f"  fx: {len(missed)} day(s) still unconverted: "
              + ", ".join(f"{d} {sorted(c)}" for d, c in sorted(missed.items())[:5]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "admob": dict(sorted(ads.items())),
            "payouts": payouts,
            "fx": rates,
            "settled": settled,
            "play": play_feed,
            "apps": apps,
        }, indent=1),
        encoding="utf-8",
    )
    print(f"wrote {OUT} — {len(apps)} apps, "
          f"{sum(len(a['downloads']) for a in apps)} app-days, {fetched} new report(s)")


if __name__ == "__main__":
    main()
