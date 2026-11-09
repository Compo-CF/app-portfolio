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
# None means "send no filter[version] at all", which is how the SALES report
# is asked for in revenue.py and the only form known to work on this account.
# Apple answers a bad combination here with "Invalid vendor number specified",
# naming a parameter that is in fact correct - so the version is discovered by
# trying, not asserted, and the real objection is logged each time.
APPLE_VERSIONS = [None, "1_4", "1_3", "1_2"]

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
        params = {"filter[frequency]": "DAILY",
                  "filter[reportType]": rtype,
                  "filter[reportSubType]": "SUMMARY",
                  "filter[vendorNumber]": vendor,
                  "filter[reportDate]": day}
        if ver:
            params["filter[version]"] = ver
        try:
            # The Accept header matters: revenue.py's working SALES call sends
            # it, this one did not, and a report asked for without it is one
            # of the ways Apple produces that misleading vendor-number error.
            r = requests.get(f"{ASC}/salesReports", timeout=60,
                             headers={"Authorization": f"Bearer {bearer}",
                                      "Accept": "application/a-gzip"},
                             params=params)
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
        # A 400 is Apple telling us *why* in the body, and the first version
        # of this swallowed it - leaving "no version accepted" with no way to
        # tell a wrong version from a wrong date from an entitlement the key
        # does not have. Keep the detail; it is the whole diagnosis.
        detail = r.text[:200]
        try:
            err = r.json()["errors"][0]
            detail = f'{err.get("code")} :: {err.get("detail", "")[:160]}'
        except Exception:                            # noqa: BLE001
            pass
        if r.status_code != 400:
            note(f"{rtype}: refused ({r.status_code}) {detail}")
            return None
        note(f"{rtype} v{ver or '(none)'}: {detail}")
    note(f"{rtype}: refused for every version tried "
         f"({', '.join(str(v or '(none)') for v in versions)})")
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
    refused = set()

    def note(msg):
        if msg not in said:
            said.add(msg)
            print(f"  subs: {msg}")
        if "refused for every version" in msg:
            refused.add(msg.split(":")[0])

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
                    note(f"SUBSCRIPTION v{ver or '(none)'} columns: "
                         f"{', '.join(head)}")
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
                    note(f"SUBSCRIPTION_EVENT v{ver or '(none)'} columns: "
                         f"{', '.join(head)}")
                per = {}
                for row in rows:
                    sku = (row.get("Subscription Apple ID")
                           or row.get("Subscription Name") or "?").strip()
                    ev = (row.get("Event") or "").strip() or "?"
                    by_ev = per.setdefault(sku, {})
                    by_ev[ev] = by_ev.get(ev, 0) + (_num(row, "Quantity") or 1)
                events[day] = per
                got_events += 1

    # A control, run only when the subscription reports were refused.
    #
    # Apple answers these with "Invalid vendor number specified" even though
    # the same vendor number fetches SALES and financeReports every day. That
    # leaves two very different explanations - this code asks wrongly, or
    # Apple has no subscription report for this account to give - and the
    # error text actively points at the wrong one.
    #
    # So ask for a report that is known to exist, through this same function,
    # with these same credentials and headers. If SALES comes back, the code
    # path is sound and the refusal belongs to the report type. That turns a
    # misleading error into a fact worth writing on the page.
    if refused:
        probe = (date.today() - timedelta(days=2)).isoformat()
        ctrl = _apple_day(probe, "SALES", [None], bearer, vendor, note)
        if ctrl:
            note(f"control: SALES for {probe} fetched fine with the same "
                 "credentials, headers and code, so the refusal above is "
                 "specific to the subscription report types - not to this "
                 "vendor number, whatever Apple's error says")
        else:
            note(f"control: SALES for {probe} returned nothing either, so "
                 "the problem may be broader than the subscription reports")

    covered = sorted(set(state) | set(events))
    through = covered[-1] if covered else None
    if got_state or got_events:
        print(f"  subs: Apple {got_state} new state day(s), "
              f"{got_events} new event day(s), through {through or '-'}")
    else:
        print("  subs: Apple published nothing new; holding "
              f"{len(covered)} day(s), through {through or '-'}")
    return {"through": through, "state": state, "events": events,
            "names": names,
            # What the page needs to say "Apple has never published one of
            # these" instead of drawing a zero as though it were measured.
            "unavailable": sorted(refused)}


def google():
    """Play subscription activity, derived from the earnings reports.

    Google publishes no subscription report in the reporting bucket for this
    account - stats/ holds crashes, installs and store_performance and
    nothing else, which a run confirmed rather than assumed. What it does
    publish is the earnings archive, and every charge there carries a
    Product Type. So a subscription's renewals are counted from its charges.

    This is a **derived** figure and labelled as one everywhere it is shown.
    A charge is a renewal that was paid; it cannot see a subscriber who has
    cancelled but not yet lapsed, nor one in a free trial who has paid
    nothing. For an account with one subscriber that distinction is academic;
    at scale it would not be, and the dashboard says so rather than implying
    Google reported a number it never reported.

    {"through": day, "derived": True, "months": {month: {sku: {...}}},
     "names": {sku: label}}
    """
    data = play.earnings() or {}
    products = data.get("products") or {}
    if not products:
        print("  subs: no Play earnings to derive subscriptions from")
        return {}

    months, names = {}, {}
    for app_id, items in products.items():
        for sku, item in items.items():
            if "subscription" not in (item.get("kind") or "").lower():
                continue
            names[sku] = item.get("title") or sku
            for day, units in (item.get("byDay") or {}).items():
                if not units:
                    continue
                slot = months.setdefault(day[:7], {}).setdefault(
                    sku, {"charges": 0, "days": []})
                slot["charges"] += units
                slot["days"].append(day)

    for by_sku in months.values():
        for slot in by_sku.values():
            slot["days"] = sorted(set(slot["days"]))

    through = data.get("through")
    total = sum(v["charges"] for m in months.values() for v in m.values())
    print(f"  subs: Play {len(names)} subscription(s), {total} charge(s) "
          f"across {len(months)} month(s), through {through or '-'}")
    return {"through": through, "derived": True,
            "months": dict(sorted(months.items())), "names": names}


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
    newest_month = max((g.get("months") or {}), default=None)
    live_play = sum(v.get("charges", 0) for v in
                    ((g.get("months") or {}).get(newest_month) or {}).values())
    print(f"wrote {OUT.name}: App Store {live_apple} active, "
          f"Play {live_play} charge(s) in {newest_month or '-'} (derived)")


if __name__ == "__main__":
    main()
