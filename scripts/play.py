"""Google Play install figures.

Play does not expose installs through the Developer API — that API publishes
releases and listings and knows nothing about downloads. The only programmatic
source is the reporting bucket Google writes CSVs into, which is the same data
the Console charts.

Two things about those files bite immediately:

  * They are **UTF-16** with a byte-order mark, not UTF-8. Read as UTF-8 they
    decode with nulls between every character and parse as one unnamed column.
  * They are **monthly**, one file per app per month, so a series spanning a
    month boundary has to be stitched from several.

The data also lags. At the time of writing Google had published through the
20th while the date was the 28th — eight days behind, far more than Apple's
one or two. The dashboard therefore states the last date it actually holds
rather than implying the series runs to today.
"""

import csv
import io
import os
import re
import zipfile
from datetime import date, datetime, timedelta

# Apple's id is the key the dashboard already uses, so Play data merges into
# the record that is there rather than creating a parallel set of apps.
PACKAGES = {
    "com.compofelice.goodshepherd": "6804512540",
    "com.compofelice.stiereats": "6773501518",
    # Draft, internal testing only, so Google publishes no report for it yet
    # and it contributes nothing. Listed now so that the day it ships, the
    # figures start arriving without anyone remembering this file exists.
    "com.compofelice.woodlandstrailguide_flutter": "6785576912",
    # Reached Play production 2026-11-01. Its reports were being read and
    # discarded until the run started naming unmapped packages.
    "com.compofelice.critterconga": "6817339425",
}

BUCKET = os.environ.get("PLAY_BUCKET", "pubsite_prod_6691100777752209003")
PREFIX = "stats/installs/"
EARNINGS = "earnings/"

# Google Cloud attributes every Storage call to a project for quota, even when
# reading a bucket the project does not own — and the Play reporting bucket is
# owned by Google, not by any project here. Any project the account can see
# will do; it is billing bookkeeping, not access control.
#
# Left to the environment rather than written down, because a project id names
# the account's internals and this repository is public. A service account key
# carries its own project, so CI needs nothing set.
PROJECT = (os.environ.get("PLAY_PROJECT")
           or os.environ.get("GOOGLE_CLOUD_PROJECT")
           or None)

# installs_<package>_<YYYYMM>_overview.csv — the per-dimension files beside it
# (country, device, carrier…) break the same totals down and are not wanted.
_NAME = re.compile(r"installs_(?P<pkg>[\w.]+)_(?P<month>\d{6})_overview\.csv$")


def _rows(blob):
    """One report's rows, decoded from whatever encoding it actually carries.

    Google writes UTF-16; the fallbacks are there so a future switch to UTF-8
    does not silently produce an empty series.
    """
    raw = blob.download_as_bytes()
    for enc in ("utf-16", "utf-8-sig", "utf-8"):
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
        if "Date" in text[:200]:
            return list(csv.DictReader(io.StringIO(text)))
    return []


def _by_name(bucket, months=14):
    """The reports fetched by their exact paths, skipping any that are absent.

    Needs only storage.objects.get, where listing needs storage.objects.list.
    Every name is `installs_<package>_<YYYYMM>_overview.csv`, so nothing has
    to be discovered — a month that does not exist simply 404s and is passed
    over, which also covers apps that had not shipped yet.

    Costs one request per package-month, all small, and the window is short
    because the account itself is new.
    """
    today = date.today()
    found = []
    for package in PACKAGES:
        for back in range(months):
            year, month = divmod((today.year * 12 + today.month - 1) - back, 12)
            name = f"{PREFIX}installs_{package}_{year}{month + 1:02d}_overview.csv"
            blob = bucket.blob(name)
            try:
                if blob.exists():
                    found.append(blob)
            except Exception:                        # noqa: BLE001
                # Forbidden rather than missing: reading is barred too, so
                # there is nothing to be gained from the remaining months.
                return found
    return found


def _whoami():
    """Which identity the request was actually made as.

    A 403 says the caller was refused; it does not say who the caller was.
    Under Workload Identity Federation there are two candidates — the
    federated principal GitHub presents, and the service account it is meant
    to impersonate — and only the service account has been granted anything
    in Play Console. If impersonation silently fails to engage, the symptom is
    an ordinary 403 and the cause is invisible. Naming the identity separates
    "not propagated yet" from "asking as the wrong principal".

    Identifiers only. Never a token.
    """
    try:
        import google.auth
        creds, project = google.auth.default()
        who = (getattr(creds, "service_account_email", None)
               or getattr(creds, "_target_principal", None)   # impersonated
               or type(creds).__name__)
        return f"{who} (quota project {project or 'unset'})"
    except Exception as e:                           # noqa: BLE001
        return f"unknown ({type(e).__name__})"


def _int(row, column):
    try:
        return int((row.get(column) or "0").strip() or 0)
    except ValueError:
        return 0


def installs():
    """Per app: daily installs and the current installed base.

    `Daily User Installs` rather than `Daily Device Installs`, because it
    counts people and so lines up with Apple's first-time downloads. One
    person with a phone and a tablet is two devices and one install, and the
    two columns do disagree in this data.

    `Active Device Installs` has no Apple equivalent at all — Apple's sales
    reports carry installs and updates as events and never report deletions,
    so an installed base cannot be derived from them. Play states it outright.

    Returns {} when the bucket cannot be read, which the caller treats as "no
    news" and leaves whatever is already committed alone. Failing that way
    round matters: a credentials problem must not silently rewrite a real
    history to zero.
    """
    try:
        from google.cloud import storage
    except ImportError:
        print("  play: google-cloud-storage not installed")
        return {}

    try:
        client = storage.Client(project=PROJECT) if PROJECT else storage.Client()
    except Exception as e:                           # noqa: BLE001 — any auth
        print(f"  play: no credentials ({type(e).__name__}: {str(e)[:120]})")
        return {}

    bucket = client.bucket(BUCKET)
    try:
        blobs = list(client.list_blobs(BUCKET, prefix=PREFIX))
    except Exception as e:                           # noqa: BLE001
        # Listing and reading are separate permissions. Play's grant is not
        # documented to include storage.objects.list, and a 403 here does not
        # mean the files are unreadable — only that the bucket cannot be
        # enumerated. Every name is predictable, so address them directly
        # rather than giving up.
        #
        # The message matters: "Project was not passed" means PLAY_PROJECT is
        # unset, which looks identical to a permissions failure without it.
        print(f"  play: cannot list the bucket ({type(e).__name__}: {str(e)[:90]})")
        print(f"  play: acting as {_whoami()}")
        print("  play: trying the files by name instead")
        blobs = _by_name(bucket)
        if not blobs:
            print("  play: no reports readable by name either")
            return {}
        print(f"  play: {len(blobs)} report(s) readable without listing")

    out, unmapped = {}, set()
    for blob in blobs:
        m = _NAME.search(blob.name)
        if not m:
            continue
        app_id = PACKAGES.get(m.group("pkg"))
        if not app_id:
            # Say so rather than drop it. Google publishes a report for every
            # app on the account, so a package missing from PACKAGES is almost
            # always an app that has just shipped on Play and whose figures
            # are now being read and thrown away — silently, which is how
            # Critter Conga went unreported after reaching production.
            unmapped.add(m.group("pkg"))
            continue
        entry = out.setdefault(app_id, {"installs": {}, "active": {}})
        for row in _rows(blob):
            day = (row.get("Date") or "").strip()
            if not day:
                continue
            entry["installs"][day] = _int(row, "Daily User Installs")
            entry["active"][day] = _int(row, "Active Device Installs")

    if unmapped:
        print("  play: reports exist for packages this dashboard does not map, "
              "so their installs are being discarded: "
              + ", ".join(sorted(unmapped)))
        print("  play: add them to PACKAGES in scripts/play.py")

    for entry in out.values():
        entry["installs"] = dict(sorted(entry["installs"].items()))
        entry["active"] = dict(sorted(entry["active"].items()))
    return out


# ---------------------------------------------------------------------------
# Earnings
#
# Installs were the only thing read here for months, while the dashboard
# printed Google Play revenue as a flat $0 with a note saying neither Play app
# sold anything. That was true when it was written and stopped being true the
# day S-Tier Eats shipped a subscription on Android: the zero was not a
# measured figure, it was a number nobody was fetching.
#
# Two things differ from the install reports:
#
#   * They are **zipped**, one archive per month, and the archive name carries
#     a merchant id written down nowhere in this repository - so they are
#     discovered by listing rather than addressed by name.
#   * They are **monthly and in arrears**. Google publishes a month's earnings
#     after that month has ended, so the current month has no report at all.
#     The coverage date is therefore reported alongside the figures, because
#     an unpublished month must not read as a month that earned nothing. That
#     distinction is the entire point of this module.
#
# A row is one financial event: a charge, Google's fee on it, tax, or a refund
# of any of those. Fees and refunds are already negative, so net proceeds are
# the plain sum and no row has to be classified by type to come out right.

_EARN = re.compile(r"earnings[-_](?P<month>\d{6})", re.I)

# Column names, lowercased. Google has shipped more than one spelling of some
# of these, so each is a list of candidates rather than a single string.
_COLS = {
    "day": ["transaction date", "order charged date", "date"],
    "amount": ["amount (merchant currency)", "amount merchant currency"],
    "currency": ["merchant currency"],
    # "Package ID" is the real one, confirmed from a published report; the
    # others are kept because Google has used them elsewhere and an extra
    # candidate costs nothing. Getting this wrong is cheap to miss and
    # expensive to notice: the file parses, every row is dropped for want of
    # an app to attach it to, and the total comes out a convincing 0.00.
    "package": ["package id", "product id", "package name", "product_id"],
}


def _pick(headers, wanted):
    """The first candidate column actually present, matched case-blind."""
    low = {(h or "").strip().lower(): h for h in headers or []}
    for cand in _COLS[wanted]:
        if cand in low:
            return low[cand]
    return None


def _day(raw):
    """Google has written this date several ways; accept the ones seen."""
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y",
                "%m/%d/%Y", "%m/%d/%y", "%d %b %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _money(raw):
    """A money cell, tolerating separators and bracketed negatives."""
    t = (raw or "").strip().replace(",", "").replace("$", "")
    if not t:
        return 0.0
    if t.startswith("(") and t.endswith(")"):
        t = "-" + t[1:-1]
    try:
        return float(t)
    except ValueError:
        return 0.0


def _coverage(blobs):
    """The last day the newest published monthly report covers.

    Taken from the file name rather than from the rows: a quiet final week
    would otherwise shorten the coverage and make a genuine zero look like a
    gap in the feed, which is the confusion this is here to prevent.
    """
    months = sorted(m.group("month") for m in
                    (_EARN.search(b.name) for b in blobs) if m)
    if not months:
        return None
    y, mo = int(months[-1][:4]), int(months[-1][4:])
    first_next = date(y + (mo == 12), 1 if mo == 12 else mo + 1, 1)
    return (first_next - timedelta(days=1)).isoformat()


def earnings():
    """Net Play proceeds per app, per currency, per day.

    Returns {"through": iso_day, "apps": {app_id: {currency: {day: amount}}}}.
    The caller needs "through" to tell "sold nothing" from "not published
    yet"; without it a month Google has not filed yet is indistinguishable
    from a month with no sales.

    Fails soft, exactly as installs() does: on any credentials or permission
    problem it says so and returns nothing, so a bad run leaves the committed
    history alone rather than rewriting real figures to zero.
    """
    try:
        from google.cloud import storage
    except ImportError:
        print("  play: google-cloud-storage not installed")
        return {}

    try:
        client = storage.Client(project=PROJECT) if PROJECT else storage.Client()
    except Exception as e:                           # noqa: BLE001
        print(f"  play: no credentials for earnings ({type(e).__name__})")
        return {}

    try:
        blobs = [b for b in client.list_blobs(BUCKET, prefix=EARNINGS)
                 if b.name.lower().endswith(".zip")]
    except Exception as e:                           # noqa: BLE001
        # No by-name fallback here, unlike installs(): the file names carry a
        # merchant id this code does not know, so without listing there is
        # nothing to ask for. Say which identity was refused.
        print(f"  play: cannot list earnings ({type(e).__name__}: {str(e)[:90]})")
        print(f"  play: acting as {_whoami()}")
        return {}

    if not blobs:
        print(f"  play: no earnings reports under {EARNINGS} in {BUCKET}")
        return {}

    apps, unmapped, shown = {}, set(), False
    for blob in sorted(blobs, key=lambda b: b.name):
        try:
            archive = zipfile.ZipFile(io.BytesIO(blob.download_as_bytes()))
        except (zipfile.BadZipFile, OSError) as e:
            print(f"  play: {blob.name} unreadable as a zip ({type(e).__name__})")
            continue

        for member in archive.namelist():
            if not member.lower().endswith(".csv"):
                continue
            raw = archive.read(member)
            text = None
            for enc in ("utf-8-sig", "utf-8", "utf-16"):
                try:
                    text = raw.decode(enc)
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue
            if text is None:
                print(f"  play: {member} is in no encoding this understands")
                continue

            reader = csv.DictReader(io.StringIO(text))
            cols = {k: _pick(reader.fieldnames, k) for k in _COLS}
            if not shown:
                # Printed once a run. The schema belongs to Google, and when
                # they change it the symptom would otherwise be a silent
                # return to zero - the precise failure this module ends.
                print("  play: earnings columns: "
                      + ", ".join(reader.fieldnames or ["(none)"]))
                shown = True
            missing = [k for k, v in cols.items() if not v]
            if missing:
                print(f"  play: {member} has no column for "
                      f"{', '.join(missing)}; skipped")
                continue

            for row in reader:
                package = (row.get(cols["package"]) or "").strip()
                app_id = PACKAGES.get(package)
                if not app_id:
                    if package:
                        unmapped.add(package)
                    continue
                day = _day(row.get(cols["day"]))
                if not day:
                    continue
                cur = (row.get(cols["currency"]) or "").strip().upper() or "USD"
                series = apps.setdefault(app_id, {}).setdefault(cur, {})
                series[day] = round(series.get(day, 0.0)
                                    + _money(row.get(cols["amount"])), 6)

    if unmapped:
        print("  play: earnings exist for packages this dashboard does not "
              "map, so their revenue is being discarded: "
              + ", ".join(sorted(unmapped)))
        print("  play: add them to PACKAGES in scripts/play.py")

    for app in apps.values():
        for cur in app:
            app[cur] = dict(sorted(app[cur].items()))

    through = _coverage(blobs)
    total = sum(v for app in apps.values()
                for days in app.values() for v in days.values())
    print(f"  play: earnings for {len(apps)} app(s) through {through or '-'}"
          f", {total:,.2f} in merchant currency")
    return {"through": through, "apps": apps}


if __name__ == "__main__":
    data = installs()
    if not data:
        print("no Play data (see above)")
    for app_id, entry in data.items():
        days = sorted(entry["installs"])
        live = [v for v in entry["active"].values() if v]
        print(f"  {app_id}: {len(days)} days {days[0]}..{days[-1]} | "
              f"installs {sum(entry['installs'].values())} | "
              f"active now {live[-1] if live else '-'}")
