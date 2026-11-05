"""Star ratings as the App Store shows them, from the public iTunes lookup.

App Store Connect's customerReviews endpoint returns only *written* reviews.
Most people rate without writing anything, so an average built from it is
the wrong number: on 2026-11-05 it put Cosmica at 3.4 from 5, while the US
store itself showed 4.07 from 14. The lookup API carries the real
averageUserRating and userRatingCount (all versions, not just the current
one), needs no credentials, and answers for every app id in a single call
per storefront.

Ratings are per storefront: an app rated in the US and the UK has two
separate averages. They are combined here weighted by count, which is sound
because a rating is a rating wherever it was left (unlike money, where
currencies must never be mixed).
"""

import time

import requests

LOOKUP = "https://itunes.apple.com/lookup"


def _one(country, ids, attempts=3):
    """{app_id: (average, count)} for one storefront, or None on failure.

    None is distinct from {}: an empty dict means the storefront answered and
    none of the apps has a rating there, while None means it did not answer
    and the caller should keep what it knew before rather than count zero.
    """
    for attempt in range(attempts):
        try:
            r = requests.get(LOOKUP, params={"id": ",".join(ids),
                                             "country": country.lower(),
                                             "entity": "software"},
                             timeout=20)
            # Apple throttles this endpoint at roughly 20 calls a minute and
            # says so with a 403 or 429 rather than slowing down.
            if r.status_code in (403, 429) or r.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code != 200:
                return None
            out = {}
            for item in r.json().get("results", []):
                count = int(item.get("userRatingCount") or 0)
                if count:
                    out[str(item.get("trackId"))] = (
                        float(item.get("averageUserRating") or 0), count)
            return out
        except (requests.RequestException, ValueError):
            time.sleep(2 * (attempt + 1))
    return None


def fetch(ids, storefronts, previous=None):
    """Per-app ratings across the given storefronts.

    Returns ({app_id: rating or None}, failed_storefronts). A rating is
        {"average": 4.32, "count": 19, "source": "itunes-lookup",
         "storefronts": {"US": {"average": 4.07, "count": 14}, ...}}
    with average/count combined across every storefront that has ratings.

    `previous` is the last run's {app_id: rating}. A storefront that fails to
    answer this run falls back to the figures it gave last time, so one
    timeout cannot make an app's count drop and then jump back.
    """
    previous = previous or {}
    per_app = {i: {} for i in ids}
    failed = []
    for n, cc in enumerate(sorted({s.upper() for s in storefronts})):
        if n:
            time.sleep(0.5)
        got = _one(cc, ids)
        if got is None:
            failed.append(cc)
            for i in ids:
                held = ((previous.get(i) or {}).get("storefronts") or {}).get(cc)
                if held:
                    per_app[i][cc] = dict(held)
            continue
        for i, (avg, count) in got.items():
            if i in per_app:
                per_app[i][cc] = {"average": round(avg, 2), "count": count,
                                  "_exact": avg}

    out = {}
    for i in ids:
        stores = per_app[i]
        total = sum(s["count"] for s in stores.values())
        if not total:
            out[i] = None
            continue
        # Weighted with Apple's unrounded figure where we have it (4.07143,
        # i.e. 57 stars over 14), so the combined average is not built from
        # averages that were already rounded.
        weighted = sum(s.get("_exact", s["average"]) * s["count"]
                       for s in stores.values()) / total
        out[i] = {
            "average": round(weighted, 2),
            "count": total,
            "source": "itunes-lookup",
            "storefronts": {
                cc: {"average": s["average"], "count": s["count"]}
                for cc, s in sorted(stores.items(),
                                    key=lambda kv: (-kv[1]["count"], kv[0]))
            },
        }
    return out, failed
