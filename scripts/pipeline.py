"""Google Play release tracks for the Pipeline tab -> data/pipeline.json.

Run by refresh.yml after it authenticates to the restaurantsfeed Workload Identity pool as
play-publisher@restaurantsfeed (read-only use here: edits are opened to list tracks and then
deleted, never committed). iOS state is not fetched here: portfolio.json already carries every
app's versions and builds from fetch.py.

Keyed by App Store app id so pipeline.js can join it to portfolio.json's apps.
Only counts are published for reviews: the repository is public.
"""
import json
import socket
from datetime import datetime, timezone
from pathlib import Path

import google.auth
from googleapiclient.discovery import build

socket.setdefaulttimeout(120)
OUT = Path(__file__).resolve().parent.parent / "data" / "pipeline.json"

# App Store id -> Play package (apps that are on Play)
PLAY = {
    "6832097718": "com.compofelice.neatly",
    "6830660705": "com.compofelice.ghostline",
    "6785576912": "com.compofelice.woodlandstrailguide_flutter",
    "6817339425": "com.compofelice.critterconga",
    "6773501518": "com.compofelice.stiereats",
    "6804512540": "com.compofelice.goodshepherd",
}


def main():
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/androidpublisher"])
    svc = build("androidpublisher", "v3", credentials=creds, cache_discovery=False)
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "play": {}}
    for app_id, pkg in PLAY.items():
        row = {"package": pkg, "tracks": {}}
        try:
            edit = svc.edits().insert(packageName=pkg, body={}).execute()["id"]
            try:
                for t in svc.edits().tracks().list(packageName=pkg, editId=edit).execute().get("tracks", []):
                    rels = [r for r in t.get("releases", []) if r.get("versionCodes")]
                    if rels:
                        r = rels[0]
                        row["tracks"][t["track"]] = {"name": r.get("name"), "status": r.get("status"),
                                                     "versionCode": r["versionCodes"][-1],
                                                     "fraction": r.get("userFraction")}
            finally:
                svc.edits().delete(packageName=pkg, editId=edit).execute()
            revs = svc.reviews().list(packageName=pkg, maxResults=50).execute().get("reviews", [])
            row["unanswered"] = sum(1 for rv in revs
                                    if not any("developerComment" in c for c in rv.get("comments", [])))
        except Exception as e:  # app not on Play yet (404) or a transient error
            row["error"] = "not on Play yet" if "404" in str(e) else type(e).__name__
        out["play"][app_id] = row
        print(app_id, pkg, list(row["tracks"]) or row.get("error"))
    OUT.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
