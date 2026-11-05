"""Historical exchange rates, so foreign proceeds can be shown in dollars.

Neither Apple report hands back one dollar figure. The financial report
settles each region in its own currency (FY2027 P01: USD, EUR, GBP, CAD,
AUD, CZK, PHP, KRW), and Apple converts those only when it pays. The daily
sales report states proceeds in the customer's currency, so a single day can read EUR 5.59, KRW 4900 and USD 3.50
at once. The dashboard used to show only the USD part, which was defensible
while foreign sales were rare and badly wrong once they were the majority —
on 31 October six of eight purchases were foreign and the page showed $5.60
of a roughly $34 day.

Rates are **per day and historical**. Converting a July sale at today's rate
would be a different kind of wrong, quietly and for ever.

Source is the European Central Bank's daily reference rates, via
api.frankfurter.app: free, no key, no registration, and published by a
central bank rather than by a vendor who can withdraw a free tier.

**These are not Apple's rates.** Apple converts at its own rate on its own
date, so every figure derived here is an estimate and is labelled as one in
the page. The finance reports are the authority; the dashboard reconciles
against them rather than assuming the difference away.
"""

import json
import urllib.error
import urllib.request
from datetime import date, timedelta

API = "https://api.frankfurter.app"


def _fetch(start, end, symbols):
    url = f"{API}/{start}..{end}?base=USD&symbols={','.join(sorted(symbols))}"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.load(r).get("rates") or {}
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        print(f"  fx: rates unavailable ({type(e).__name__}); keeping what is held")
        return {}


def needed(apps):
    """Which (day, currency) pairs carry foreign proceeds worth converting."""
    out = {}
    for app in apps:
        for cur, days in (app.get("revenue") or {}).items():
            if cur == "USD":
                continue
            for day, amount in days.items():
                if amount:
                    out.setdefault(day, set()).add(cur)
    return out


def rates(wanted, held=None):
    """USD per one unit of each currency, for every day that needs one.

    Rates already held are never refetched. A past day's ECB reference rate
    does not change, and a run that cannot reach the network should leave the
    converted history exactly as it was rather than blanking it.
    """
    held = {d: dict(v) for d, v in (held or {}).items()}
    missing = {d: {c for c in cs if c not in held.get(d, {})}
               for d, cs in wanted.items()}
    missing = {d: cs for d, cs in missing.items() if cs}
    if not missing:
        return held

    symbols = sorted({c for cs in missing.values() for c in cs})
    # Reach back a fortnight before the earliest gap. The ECB does not publish
    # at weekends or on its own holidays, so a sale made on a Sunday has no
    # rate of its own and has to borrow the preceding working day's.
    start = (date.fromisoformat(min(missing)) - timedelta(days=14)).isoformat()
    published = _fetch(start, max(missing), symbols)
    if not published:
        return held

    days = sorted(published)
    added = 0
    for day in sorted(missing):
        prior = [p for p in days if p <= day]      # latest publication by then
        if not prior:
            print(f"  fx: no rate published on or before {day}; skipped")
            continue
        quoted = published[prior[-1]]
        for cur in missing[day]:
            rate = quoted.get(cur)
            if not rate:
                continue
            # Frankfurter quotes USD -> currency. The dashboard multiplies, so
            # store the inverse: dollars for one unit.
            held.setdefault(day, {})[cur] = round(1.0 / rate, 6)
            added += 1

    print(f"  fx: {added} rate(s) added across {len(missing)} day(s)")
    return held


def in_usd(revenue, fx):
    """One app's proceeds as dollars per day, foreign sales converted.

    A day whose rate is missing contributes its USD part only, and is handed
    back as unconverted rather than silently dropped — a converted total that
    quietly omits a currency is the exact failure this module exists to end.
    """
    out, unconverted = {}, {}
    for cur, days in (revenue or {}).items():
        for day, amount in days.items():
            if not amount:
                continue
            if cur == "USD":
                out[day] = round(out.get(day, 0) + amount, 4)
                continue
            rate = (fx.get(day) or {}).get(cur)
            if rate is None:
                unconverted.setdefault(day, {})[cur] = amount
                continue
            out[day] = round(out.get(day, 0) + amount * rate, 4)
    return out, unconverted
