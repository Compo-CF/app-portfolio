"""Subscriptions: how many there are, and what happened to them.

Money is already covered twice over - finance.py settles it and revenue.py
splits it by product. Neither can say how many people are *subscribed*, which
is the only number that says whether a subscription business exists at all. A
month of renewals and a month of first-time sales look identical in a revenue
report and mean entirely different things.

Both stores publish this, in quite different shapes:

  * **Apple** - `salesReports` with reportType SUBSCRIPTION (a daily snapshot:
    how many active, by state, by product) and SUBSCRIPTION_EVENT (what moved
    that day: subscribed, cancelled, renewed, refunded). Daily, roughly a day
    behind, in the same gzipped TSV as every other Apple report.
  * **Google** - the reporting bucket's subscription CSVs, UTF-16 like the
    install files, monthly per package, and in arrears.

Neither is guaranteed to exist. Apple publishes nothing for a day with no
subscription activity and 404s rather than returning an empty report; Google
writes no file for an app that has never sold one. A missing report is
therefore normal and is never treated as an error - but it is said out loud,
every run, because "no subscribers" and "nobody looked" have already been
confused once on this dashboard and the Play revenue zero was the result.

Written to data/subs.json, its own file: it moves on a different clock from
portfolio.json and has no reason to contend with it.
"""

import gzip
import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import jwt
import requests

import play

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "subs.json"
ASC = "https://api.appstoreconnect.apple.com/v1"

# These reports are versioned separately from the sales ones, and asking for a
# version the account cannot serve is a 400 rather than a 404 - so the newest
# is tried first and older ones are a fallback, not an assumption.
APPLE_VERSIONS = ["1_4", "1_3", "1_2"]

# How far back a first run reaches. Apple keeps these about a year; there is
# no point asking for longer than the subscriptions have existed.
BACKFILL = int(os.environ.get("SUBS_BACKFILL_DAYS", "120"))

PLAY_PREFIX = "stats/subscriptions/"
_PLAY_NAME = re.compile(
    r"subscriptions_(?P<pkg>[\w.]+)_(?P<month>\d{6})_overview\.csv$", re.I)


def _creds():
    key = os.environ.get("ASC_PRIVATE_KEY")
    key_id = os.environ.get("ASC_KEY_ID")
    issuer = os.environ.get("ASC_ISSUER_ID")
    vendor = os.environ.get("ASC_VENDOR_NUMBER")
    if not all((key, key_id, issuer, vendor)):
        print("  subs: Apple credentials not set; skipping the App Store side")
        return None
    return key.replace("\\n", "\n"), key_id, issuer, vendor


def _token(key, key_id, issuer):
    now = int(time.time())
    return jwt.encode({"iss": issuer, "iat": now, "exp": now + 1200,
                       "aud": "appstoreconnect-v1"}, key,
                      algorithm="ES256", headers={"kid": key_id, "typ": "JWT"})


def _rows(text):
    """Apple's reports are tab separated with one header line."""
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 2:
        return [], []
    head = [h.strip() for h in lines[0].split("\t")]
    out = []
    for line in lines[1:]:
        cells = line.split("\t")
        if len(cells) < 2:
            continue
        out.append(dict(zip(head, [c.strip() for c in cells])))
    return head, out


def _num(row, *names):
    """The first of these columns that exists, as a whole number."""
    for n in names:
        if n in row:
            try:
                return int(float((row[n] or "0").replace(",", "") or 0))
            except ValueError:
                return 0
    return 0


def _apple_day(day, rtype, versions, bearer, vendor, note):
    """One report for one day, or None where Apple has none.

    Returns (version, header, rows). A 404 means no activity that day and is
    the ordinary answer, so it is silent; a 400 means the version is not
    served and the next is tried; anything else is said once.
    """
    for ver in versions:
        try:
            r = requests.get(f"{ASC}/salesReports", timeout=60,
                             headers={"Authorization": f"Bearer {bearer}"},
                             params={"filter[frequency]": "DAILY",
                                     "filter[reportType]": rtype,
                                     "filter[reportSubType]": "SUMMARY",
                                     "filter[vendorNumber]": vendor,
                                     "filter[version]": ver,
                                     "filter[reportDate]": day})
        except requests.RequestException as e:
            note(f"{rtype} {day}: unreachable ({type(e).__name__})")
            return None
        if r.status_code == 404:
            return None
        if r.status_code == 200:
            try:
                text = gzip.decompress(r.content).decode("utf-8", "replace")
            except (OSError, EOFError):
                text = r.content.decode("utf-8", "replace")
            head, rows = _rows(text)
            return ver, head, rows
        if r.status_code != 400:
            note(f"{rtype}: refused ({r.status_code}) {r.text[:90]}")
            return None
    note(f"{rtype}: no version accepted of {', '.join(versions)}")
    return None


def apple(days_back=BACKFILL, held=None):
    """Apple's daily subscription state and events, merged onto what is held.

    {"through": day,
     "state":  {day: {sku: {"active": n, "trial": n, "intro": n, "grace": n}}},
     "events": {day: {sku: {event_name: n}}},
     "names":  {sku: "Subscription (App)"}}
    """
    creds = _creds()
    if not creds:
        return {}
    key, key_id, issuer, vendor = creds
    bearer = _token(key, key_id, issuer)

    held = held or {}
    state = dict(held.get("state") or {})
    events = dict(held.get("events") or {})
    names = dict(held.get("names") or {})

    said = set()

    def note(msg):
        if msg not in said:
            said.add(msg)
            print(f"  subs: {msg}")

    # Apple files a day the following morning Pacific, so yesterday is the
    # newest that can exist. Days already held are never re-asked: a closed
    # day's snapshot does not change.
    today = date.today()
    wanted = [(today - timedelta(days=n)).isoformat()
              for n in range(1, days_back + 1)]
    shown, got_state, got_events = set(), 0, 0

    for day in wanted:
        if day not in state:
            res = _apple_day(day, "SUBSCRIPTION", APPLE_VERSIONS,
                             bearer, vendor, note)
            if res:
                ver, head, rows = res
                if "state" not in shown:
                    shown.add("state")
                    note(f"SUBSCRIPTION v{ver} columns: {', '.join(head)}")
                per = {}
                for row in rows:
                    sku = (row.get("Subscription Apple ID")
                           or row.get("Subscription Name") or "?").strip()
                    label = (row.get("Subscription Name") or "").strip()
                    app = (row.get("App Name") or "").strip()
                    if label:
                        names[sku] = label + (f" ({app})" if app else "")
                    slot = per.setdefault(sku, {"active": 0, "trial": 0,
                                                "intro": 0, "grace": 0})
                    slot["active"] += _num(row, "Active Standard Price Subscriptions")
                    slot["trial"] += _num(
                        row, "Active Free Trial Introductory Offer Subscriptions",
                        "Free Trial Introductory Offer Subscriptions")
                    slot["intro"] += _num(
                        row, "Active Pay Up Front Introductory Offer Subscriptions",
                        "Active Pay As You Go Introductory Offer Subscriptions")
                    slot["grace"] += _num(row, "Grace Period Subscriptions",
                                          "Billing Retry Subscriptions")
                state[day] = per
                got_state += 1

        if day not in events:
            res = _apple_day(day, "SUBSCRIPTION_EVENT", APPLE_VERSIONS,
                             bearer, vendor, note)
            if res:
                ver, head, rows = res
                if "events" not in shown:
                    shown.add("events")
                    note(f"SUBSCRIPTION_EVENT v{ver} columns: {', '.join(head)}")
                per = {}
                for row in rows:
                    sku = (row.get("Subscription Apple ID")
                           or row.get("Subscription Name") or "?").strip()
                    ev = (row.get("Event") or "").strip() or "?"
                    by_ev = per.setdefault(sku, {})
                    by_ev[ev] = by_ev.get(ev, 0) + (_num(row, "Quantity") or 1)
                events[day] = per
                got_events += 1

    covered = sorted(set(state) | set(events))
    through = covered[-1] if covered else None
    if got_state or got_events:
        print(f"  subs: Apple {got_state} new state day(s), "
              f"{got_events} new event day(s), through {through or '-'}")
    else:
        print("  subs: Apple published nothing new; holding "
              f"{len(covered)} day(s), through {through or '-'}")
    return {"through": through, "state": state, "events": events, "names": names}


def google():
    """Play's subscription counts per package.

    Same bucket and the same UTF-16 encoding as the install reports, under a
    different prefix. {"through": day, "days": {day: {sku: {...}}}, "names": {}}
    """
    try:
        from google.cloud import storage
    except ImportError:
        print("  subs: google-cloud-storage not installed")
        return {}
    try:
        client = (storage.Client(project=play.PROJECT) if play.PROJECT
                  else storage.Client())
    except Exception as e:                           # noqa: BLE001
        print(f"  subs: no Google credentials ({type(e).__name__})")
        return {}

    try:
        blobs = list(client.list_blobs(play.BUCKET, prefix=PLAY_PREFIX))
    except Exception as e:                           # noqa: BLE001
        print(f"  subs: cannot list {PLAY_PREFIX} "
              f"({type(e).__name__}: {str(e)[:80]})")
        return {}

    if not blobs:
        print(f"  subs: Google has nothing under {PLAY_PREFIX}")
        return {}

    days, names, shown = {}, {}, False
    for blob in sorted(blobs, key=lambda b: b.name):
        m = _PLAY_NAME.search(blob.name)
        if not m:
            continue
        if not play.PACKAGES.get(m.group("pkg")):
            print(f"  subs: no app mapped for package {m.group('pkg')}; skipped")
            continue
        rows = play._rows(blob)                      # noqa: SLF001
        if rows and not shown:
            shown = True
            print(f"  subs: Play columns: {', '.join(rows[0].keys())}")
        for row in rows:
            day = (row.get("Date") or "").strip()
            sku = (row.get("Subscription ID") or row.get("Sku Id")
                   or row.get("Product ID") or "").strip()
            if not day or not sku:
                continue
            names.setdefault(sku, sku)
            slot = days.setdefault(day, {}).setdefault(sku, {})
            slot["active"] = _num(row, "Active Subscriptions")
            slot["new"] = _num(row, "New Subscriptions")
            slot["cancelled"] = _num(row, "Cancelled Subscriptions")

    through = max(days) if days else None
    print(f"  subs: Play {len(days)} day(s), through {through or '-'}")
    return {"through": through, "days": days, "names": names}


def main():
    prev = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
        except ValueError:
            prev = {}

    # A first run reaches back; later ones only need the days since. Asking
    # Apple for 120 days on every run is 240 requests to be told nothing.
    held_days = len((prev.get("apple") or {}).get("state") or {})
    back = BACKFILL if not held_days else int(os.environ.get("SUBS_DAYS", "10"))

    a = apple(back, prev.get("apple")) or prev.get("apple") or {}
    g = google() or prev.get("play") or {}

    out = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "apple": a, "play": g}
    if prev and {k: v for k, v in out.items() if k != "generated"} == \
            {k: v for k, v in prev.items() if k != "generated"}:
        print("subs: nothing new")
        return

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")

    live_apple = sum(v.get("active", 0) + v.get("trial", 0) for v in
                     ((a.get("state") or {}).get(a.get("through")) or {}).values())
    live_play = sum(v.get("active", 0) for v in
                    ((g.get("days") or {}).get(g.get("through")) or {}).values())
    print(f"wrote {OUT.name}: App Store {live_apple} active, "
          f"Play {live_play} active")


if __name__ == "__main__":
    main()
