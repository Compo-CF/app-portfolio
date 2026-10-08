"""Apple's financial reports: what is actually owed, period by period.

The daily sales report estimates proceeds. This is the settlement view — the
figure Apple pays, after adjustments and after every foreign sale has been
converted. For a vendor banking in the US the whole report comes back in USD,
including sales made in won, euros, pesos and pounds, which is why this and
the per-app revenue figures do not line up and should not be made to.

Two things about `filter[reportDate]` are worth knowing before changing it:

  * **It is a fiscal label, not a calendar month.** "2026-10" is fiscal year
    2026, period 10, and it covers 28 June to 1 August 2026. Asking for the
    month you mean gets a 404 that reads exactly like "no sales".
  * **Fiscal years roll over in late September.** The period after FY2026 P12
    (ending 26 September 2026) is FY2027 P01, so a scan that only walks the
    period number forward stops dead at the turn of the fiscal year.

A 404 means "no report under that label" and is the normal answer for a
period that has not closed yet, so it is never treated as an error.
"""

import gzip
from datetime import date, datetime

import requests

ASC = "https://api.appstoreconnect.apple.com/v1"

# Apple's fiscal periods are four or five weeks. Used only to decide how far
# forward it is worth probing; the real lengths come from the reports.
PERIOD_DAYS = 28


def _label_order(label):
    y, p = label.split("-")
    return (int(y), int(p))


def _fetch(label, bearer, vendor):
    """One report as a list of per-currency rows, or None if Apple has none."""
    try:
        r = requests.get(f"{ASC}/financeReports", timeout=60,
                         headers={"Authorization": f"Bearer {bearer}"},
                         params={"filter[regionCode]": "ZZ",
                                 "filter[reportDate]": label,
                                 "filter[reportType]": "FINANCIAL",
                                 "filter[vendorNumber]": vendor})
    except requests.RequestException as e:
        print(f"  finance {label}: unreachable ({type(e).__name__})")
        return None

    if r.status_code == 404:
        return None                      # not closed yet, or no sales
    if r.status_code != 200:
        # Worth saying out loud: a 401 here means the key lost Finance access,
        # which otherwise looks identical to "no reports exist".
        print(f"  finance {label}: refused ({r.status_code})")
        return None

    try:
        text = gzip.decompress(r.content).decode("utf-8", "replace")
    except (OSError, EOFError):
        text = r.content.decode("utf-8", "replace")

    rows = text.splitlines()
    if len(rows) < 2:
        return None

    start = end = None
    by_currency = {}
    for line in rows[1:]:
        c = line.split("\t")
        # The file ends with a "Total_Rows" line and then a second, differently
        # shaped country summary table. Stop at the first non-detail row
        # rather than trying to parse both.
        if len(c) < 9 or not c[0] or c[0] == "Total_Rows":
            break
        start = start or c[0]
        end = c[1]
        try:
            by_currency[c[8]] = by_currency.get(c[8], 0.0) + float(c[7] or 0)
        except ValueError:
            continue

    if not start or not by_currency:
        return None

    def iso(s):
        return datetime.strptime(s, "%m/%d/%Y").date().isoformat()

    return [{"period": label, "start": iso(start), "end": iso(end),
             "currency": cur, "amount": round(amt, 2)}
            for cur, amt in sorted(by_currency.items())]


def periods(bearer, vendor, known=None, today=None):
    """Every financial report Apple holds, merged onto the ones already known.

    Probing is bounded in both directions so this does not spend a minute of
    every run asking about periods that will never exist:

      * Nothing known yet: sweep this fiscal year and the next, twelve labels
        each, once.
      * Something known: never look earlier than the oldest report already
        held — a period that closed without sales will not grow one — and
        look forward only as many periods as could plausibly have closed
        since the newest one.
    """
    today = today or date.today()
    held = {}
    for p in known or []:
        held[f"{p['period']}|{p['currency']}"] = p

    labels_held = {p["period"] for p in held.values()}
    if held:
        newest_end = max(date.fromisoformat(p["end"]) for p in held.values())
        ahead = max(1, (today - newest_end).days // PERIOD_DAYS + 2)
        floor = min(_label_order(l) for l in labels_held)
    else:
        ahead, floor = None, None

    candidates = []
    for y in (today.year, today.year + 1):
        for p in range(1, 13):
            label = f"{y}-{p:02d}"
            if label in labels_held:
                continue
            if floor and _label_order(label) < floor:
                continue
            candidates.append(label)
    candidates.sort(key=_label_order)

    if ahead is not None:
        # Only the next few labels after the newest one held.
        candidates = candidates[:ahead]

    found = 0
    for label in candidates:
        rows = _fetch(label, bearer, vendor)
        if not rows:
            continue
        found += 1
        for row in rows:
            held[f"{row['period']}|{row['currency']}"] = row

    if found:
        print(f"  finance: {found} new report(s), {len(held)} held")
    return sorted(held.values(), key=lambda p: (p["end"], p["currency"]))
