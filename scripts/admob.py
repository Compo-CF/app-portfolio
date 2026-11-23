"""AdMob earnings, which Apple's reports know nothing about.

The dashboard's money figures come from App Store Connect and therefore cover
in-app purchases only. Ad income is a different account entirely, and for apps
that carry ads it can be most of what they make — so "proceeds" without it is
a half-answer that reads like a whole one.

**This cannot use the service account built for Google Play.** AdMob accounts
belong to a Google *user*, not a Cloud project, and the API does not accept
service-account credentials. The only way to reach it unattended is a stored
OAuth refresh token: granted once by a human, exchanged for short-lived access
tokens thereafter.

Four values, all from the environment, none ever logged:

    ADMOB_PUBLISHER_ID      pub-0000000000000000
    ADMOB_CLIENT_ID         an OAuth client of type "Desktop app"
    ADMOB_CLIENT_SECRET     its secret
    ADMOB_REFRESH_TOKEN     from one consent run, scope admob.readonly

Absent any of them this returns nothing and the caller carries on, because an
incomplete money figure is worth more than no dashboard.
"""

import os
from datetime import date, timedelta

import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://admob.googleapis.com/v1"


def _access_token():
    """A short-lived token, or None when the credentials are not configured."""
    client_id = os.environ.get("ADMOB_CLIENT_ID")
    secret = os.environ.get("ADMOB_CLIENT_SECRET")
    refresh = os.environ.get("ADMOB_REFRESH_TOKEN")
    if not (client_id and secret and refresh):
        return None

    r = requests.post(TOKEN_URL, timeout=30, data={
        "client_id": client_id,
        "client_secret": secret,
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    })
    if r.status_code != 200:
        # Deliberately not echoing the body: it can carry the client id, and a
        # failure here is almost always "the refresh token was revoked".
        print(f"  admob: token refresh failed ({r.status_code})")
        return None
    return r.json().get("access_token")


def _error_message(response):
    """Google's reason for refusing, from either shape it arrives in.

    networkReport:generate streams a JSON array, so a failure comes back as
    [{"error": {...}}] rather than the bare object the rest of Google's APIs
    return. Assuming the object shape raises AttributeError on a list and
    turns a handled refusal into a crashed job — which is exactly what it did
    the first time.
    """
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, list):
        body = next((x for x in body if isinstance(x, dict) and "error" in x), {})
    if not isinstance(body, dict):
        return ""
    return ((body.get("error") or {}).get("message") or "")


def earnings(days=120):
    """Estimated earnings per day across the whole AdMob account.

    Account-wide rather than per app. AdMob identifies apps by its own ids and
    display names, neither of which maps cleanly onto an App Store id — and a
    guessed mapping would put one app's income against another, which is worse
    than reporting a total honestly.

    Returns {"YYYY-MM-DD": usd}, or {} when unconfigured or unreachable.
    """
    publisher = os.environ.get("ADMOB_PUBLISHER_ID")
    token = _access_token()
    if not (publisher and token):
        if publisher or token:
            print("  admob: partly configured — all four values are needed")
        return {}

    end = date.today()
    start = end - timedelta(days=days)
    body = {"reportSpec": {
        "dateRange": {
            "startDate": {"year": start.year, "month": start.month, "day": start.day},
            "endDate": {"year": end.year, "month": end.month, "day": end.day},
        },
        "dimensions": ["DATE"],
        "metrics": ["ESTIMATED_EARNINGS"],
    }}

    try:
        r = requests.post(f"{API}/accounts/{publisher}/networkReport:generate",
                          json=body, timeout=60,
                          headers={"Authorization": f"Bearer {token}"})
    except requests.RequestException as e:
        print(f"  admob: unreachable ({type(e).__name__})")
        return {}

    if r.status_code != 200:
        # Google says why, and the reasons need different fixes: the API not
        # enabled on the project, a publisher id that is not this user's, or
        # a scope that was never granted. A bare status code sends you
        # guessing. Safe to show — it names APIs and projects, not secrets.
        reason = _error_message(r)
        print(f"  admob: report refused ({r.status_code})"
              + (f" — {reason[:200]}" if reason else ""))
        return {}

    out = {}
    for chunk in r.json():
        row = chunk.get("row")
        if not row:
            continue
        day = (row.get("dimensionValues", {}).get("DATE", {}) or {}).get("value")
        micros = (row.get("metricValues", {}).get("ESTIMATED_EARNINGS", {})
                  or {}).get("microsValue")
        if not day or micros is None:
            continue
        # YYYYMMDD in the report; micros because money in floats is a mistake
        # Google declines to make.
        key = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        out[key] = round(int(micros) / 1_000_000, 2)
    return out


def _app_ids(publisher, token, packages):
    """AdMob app id (ca-app-pub-...~n) -> App Store id, from AdMob's own store links.

    Every AdMob app that has been linked to its store listing carries
    linkedAppInfo.appStoreId: the numeric App Store id on iOS, the package name
    on Android. That is AdMob's own statement of which store app it is, so no
    name matching is involved. Android packages are turned into App Store ids
    with `packages` (play.PACKAGES), because the dashboard keys apps by their
    App Store id. Unlinked apps map to None and are reported by display name.
    """
    out, names, page = {}, {}, None
    while True:
        params = {"pageSize": 100}
        if page:
            params["pageToken"] = page
        r = requests.get(f"{API}/accounts/{publisher}/apps", params=params, timeout=60,
                         headers={"Authorization": f"Bearer {token}"})
        if r.status_code != 200:
            print(f"  admob: apps list refused ({r.status_code}) {_error_message(r)[:160]}")
            return {}, {}
        body = r.json()
        for a in body.get("apps", []):
            linked = (a.get("linkedAppInfo") or {}).get("appStoreId")
            names[a["appId"]] = ((a.get("linkedAppInfo") or {}).get("displayName")
                                 or (a.get("manualAppInfo") or {}).get("displayName") or a["appId"])
            if linked and a.get("platform") == "ANDROID":
                linked = packages.get(linked)
            out[a["appId"]] = linked or None
        page = body.get("nextPageToken")
        if not page:
            return out, names


def earnings_by_app(packages, days=120):
    """Estimated earnings per day for each app, keyed by App Store id.

    Returns {"apps": {app_store_id: {"YYYY-MM-DD": usd}},
             "unlinked": {display_name: {"YYYY-MM-DD": usd}}}, or {} when
    AdMob is not configured or refuses. iOS and Android earnings of the same
    app are added together under its App Store id.
    """
    publisher = os.environ.get("ADMOB_PUBLISHER_ID")
    token = _access_token()
    if not (publisher and token):
        return {}
    ids, names = _app_ids(publisher, token, packages)
    end = date.today()
    start = end - timedelta(days=days)
    body = {"reportSpec": {
        "dateRange": {
            "startDate": {"year": start.year, "month": start.month, "day": start.day},
            "endDate": {"year": end.year, "month": end.month, "day": end.day},
        },
        "dimensions": ["DATE", "APP"],
        "metrics": ["ESTIMATED_EARNINGS"],
    }}
    try:
        r = requests.post(f"{API}/accounts/{publisher}/networkReport:generate",
                          json=body, timeout=60, headers={"Authorization": f"Bearer {token}"})
    except requests.RequestException as e:
        print(f"  admob by app: unreachable ({type(e).__name__})")
        return {}
    if r.status_code != 200:
        print(f"  admob by app: report refused ({r.status_code}) {_error_message(r)[:160]}")
        return {}
    apps, unlinked = {}, {}
    for chunk in r.json():
        row = chunk.get("row")
        if not row:
            continue
        dims = row.get("dimensionValues", {})
        day = (dims.get("DATE") or {}).get("value")
        admob_app = (dims.get("APP") or {}).get("value")
        micros = (row.get("metricValues", {}).get("ESTIMATED_EARNINGS", {}) or {}).get("microsValue")
        if not day or not admob_app or micros is None:
            continue
        key = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        usd = int(micros) / 1_000_000
        store_id = ids.get(admob_app)
        bucket = apps.setdefault(store_id, {}) if store_id else unlinked.setdefault(
            names.get(admob_app) or (dims.get("APP") or {}).get("displayLabel") or admob_app, {})
        bucket[key] = bucket.get(key, 0) + usd
    for series in list(apps.values()) + list(unlinked.values()):
        for k in series:
            series[k] = round(series[k], 2)
    if unlinked:
        print("  admob by app: not linked to a store listing:", ", ".join(sorted(unlinked)))
    return {"apps": apps, "unlinked": unlinked}


if __name__ == "__main__":
    data = earnings()
    if not data:
        print("no AdMob data (see above)")
    else:
        total = sum(data.values())
        days = sorted(data)
        print(f"  {len(days)} days {days[0]}..{days[-1]} — ${total:.2f} estimated")
