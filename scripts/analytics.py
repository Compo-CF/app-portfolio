"""First-time downloads per UTC day, from Apple's Analytics Reports API.

Why not the sales report: the daily sales summary buckets by Pacific-time day
and counts download *units* by product type, so its days never line up with
App Store Connect's App Analytics pages, which use UTC days and count each
Apple ID's first download once. A spike in a US evening landed on different
days in the two (Cosmica, 11 Oct 2026: 52 here vs 28 in App Analytics), which
made the dashboard disagree with the page Anthony checks it against.

How the API works (Apple, "Downloading analytics reports"):
  1. POST an analyticsReportRequest per app. ONGOING keeps producing reports;
     ONE_TIME_SNAPSHOT returns history back to 1 Jan 2024 once. The first
     reports take 1-2 days to appear after a request is created.
  2. Each request has reports; the one wanted is "App Downloads Standard",
     category COMMERCE.
  3. Each report has instances (one per processingDate and granularity).
     An instance can restate full data for past dates, so later instances
     override earlier ones day by day.
  4. Each instance has one or more gzipped segments: delimited text with
     Date, App Apple Identifier, Download Type, ... , Counts.

Only "First-time Download" rows are counted, matching what the dashboard has
always meant by a download.

Creating requests needs an Admin key; listing and downloading also works with
Sales and Reports. An ONGOING request that goes unread for a long time becomes
stoppedDueToInactivity, and ensure_requests replaces it.
"""

import csv
import gzip
import io

import requests

ASC = "https://api.appstoreconnect.apple.com/v1"
DOWNLOAD_TYPE = "first-time download"


class Client:
    def __init__(self, token_fn):
        self._token = token_fn

    def _headers(self):
        return {"Authorization": f"Bearer {self._token()}"}

    def get(self, path_or_url, params=None):
        url = path_or_url if path_or_url.startswith("http") else f"{ASC}/{path_or_url}"
        r = requests.get(url, headers=self._headers(), params=params, timeout=60)
        if r.status_code != 200:
            # Loud rather than silent: a bad filter value comes back as a 400
            # and would otherwise look exactly like "no reports yet".
            print(f"  analytics: GET {url.split('/v1/')[-1]} -> HTTP {r.status_code}")
            return None
        return r.json()

    def get_all(self, path, params=None):
        """Every item across pages (follows links.next)."""
        items, page = [], self.get(path, params)
        while page:
            items.extend(page.get("data") or [])
            nxt = (page.get("links") or {}).get("next")
            page = self.get(nxt) if nxt else None
        return items

    def post(self, path, body):
        r = requests.post(f"{ASC}/{path}", headers=self._headers(), json=body, timeout=60)
        try:
            payload = r.json() if r.content else {}
        except ValueError:
            payload = {}
        return r.status_code, payload


def ensure_requests(client, app_id):
    """The app's usable report requests, creating whatever is missing.

    Returns (ongoing_ids, snapshot_ids). Creating fails without an Admin key;
    that is reported and skipped rather than fatal, so the rest of the
    dashboard still refreshes.
    """
    existing = client.get_all(f"apps/{app_id}/analyticsReportRequests")
    ongoing, snapshot = [], []
    for item in existing:
        a = item.get("attributes") or {}
        if a.get("stoppedDueToInactivity"):
            continue
        if a.get("accessType") == "ONGOING":
            ongoing.append(item["id"])
        elif a.get("accessType") == "ONE_TIME_SNAPSHOT":
            snapshot.append(item["id"])

    for access, held in (("ONGOING", ongoing), ("ONE_TIME_SNAPSHOT", snapshot)):
        if held:
            continue
        status, body = client.post("analyticsReportRequests", {
            "data": {
                "type": "analyticsReportRequests",
                "attributes": {"accessType": access},
                "relationships": {"app": {"data": {"type": "apps", "id": app_id}}},
            }
        })
        if status in (200, 201) and (body.get("data") or {}).get("id"):
            held.append(body["data"]["id"])
            print(f"  analytics: requested {access} for {app_id} (first data in 1-2 days)")
        else:
            print(f"  analytics: could not request {access} for {app_id}: HTTP {status}")
    return ongoing, snapshot


def _downloads_report_ids(client, request_id):
    """The standard App Store Downloads report(s) under one request."""
    out = []
    # The API's category value is COMMERCE (the docs' prose calls it "App
    # Store Commerce"; APP_STORE_COMMERCE is rejected with a 400).
    for item in client.get_all(f"analyticsReportRequests/{request_id}/reports",
                               {"filter[category]": "COMMERCE"}):
        name = ((item.get("attributes") or {}).get("name") or "").lower()
        # Apple's names read like "App Downloads Standard". Matched loosely so a
        # rename to "App Store Downloads Standard" doesn't silently drop it, and
        # the detailed variant (same counts, more privacy noise) is skipped.
        if "download" in name and "standard" in name and "pre-order" not in name:
            out.append(item["id"])
    return out


def _parse_segment(raw, app_id):
    """{date: first-time downloads} from one decompressed segment."""
    text = raw.decode("utf-8-sig", "replace")
    first = text.split("\n", 1)[0]
    delim = "\t" if first.count("\t") > first.count(",") else ","
    totals = {}
    for row in csv.DictReader(io.StringIO(text), delimiter=delim):
        if (row.get("App Apple Identifier") or "").strip() not in ("", app_id):
            continue
        if (row.get("Download Type") or "").strip().lower() != DOWNLOAD_TYPE:
            continue
        day = (row.get("Date") or "").strip()[:10]
        try:
            n = int(float(row.get("Counts") or 0))
        except ValueError:
            continue
        if day:
            totals[day] = totals.get(day, 0) + n
    return totals


def _instance_totals(client, instance_id, app_id):
    totals = {}
    for seg in client.get_all(f"analyticsReportInstances/{instance_id}/segments"):
        url = (seg.get("attributes") or {}).get("url")
        if not url:
            continue
        # Presigned download link: no Authorization header, or the storage
        # host rejects the request.
        r = requests.get(url, timeout=120)
        if r.status_code != 200:
            continue
        try:
            raw = gzip.decompress(r.content)
        except OSError:
            raw = r.content
        for day, n in _parse_segment(raw, app_id).items():
            totals[day] = totals.get(day, 0) + n
    return totals


def first_time_downloads(client, app_id, request_ids, since=None):
    """{UTC date: first-time downloads} from every daily instance of the
    downloads report under the given requests.

    Instances are applied oldest processingDate first and each one replaces
    the days it covers, because Apple restates full data for past dates in
    later instances (late-arriving events, corrections). `since` limits the
    walk to instances processed on or after that ISO date.
    """
    instances = []
    for rid in request_ids:
        for report_id in _downloads_report_ids(client, rid):
            for inst in client.get_all(f"analyticsReports/{report_id}/instances",
                                       {"filter[granularity]": "DAILY"}):
                pdate = ((inst.get("attributes") or {}).get("processingDate") or "")[:10]
                if since and pdate and pdate < since:
                    continue
                instances.append((pdate, inst["id"]))

    out = {}
    for _, inst_id in sorted(instances):
        out.update(_instance_totals(client, inst_id, app_id))
    return out
