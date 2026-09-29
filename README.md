# App Portfolio

A daily view of the App Store Connect numbers for every app I have shipped.

**Live at:** https://compo-cf.github.io/app-portfolio/

Downloads are first-time installs only — updates and re-downloads have their
own product-type codes in Apple's sales reports and are deliberately
excluded, because an update is not a new person.

## How it works

A scheduled GitHub Action runs `scripts/fetch.py`, which pulls from the App
Store Connect API and commits `data/portfolio.json`. The page is static and
reads that file in the browser. Nothing server-side, and no credentials ever
reach the client.

Download history accumulates in the committed JSON, so it will eventually
reach further back than the year Apple itself keeps.

## `data/portfolio.json` belongs to the Action

The scheduled job is its only writer. Running `scripts/fetch.py` locally is
how you test a parser change, but **do not commit what it produces** — a
local commit and a scheduled one touching the same file collide, and the
resolution is never interesting because the file is derived.

```sh
python scripts/fetch.py          # regenerate, look at it
git checkout data/portfolio.json # then throw it away
```

Push the parser change on its own, then run the workflow to put the new
fields live:

```sh
gh workflow run refresh.yml
```

The job survives a race from the other direction. If something lands while
it is running, its push is rejected, and it resets onto the new head and
regenerates instead of forcing its own copy over the top — which matters,
because a run that started before a parser change would otherwise overwrite
the new fields with output from the old code.

## Secrets

Set these as repository secrets; they are never committed:

| Secret | |
|---|---|
| `ASC_KEY_ID` | App Store Connect API key id |
| `ASC_ISSUER_ID` | issuer id |
| `ASC_PRIVATE_KEY` | the full contents of the .p8 file |
| `ASC_VENDOR_NUMBER` | vendor number, for sales reports |

Running the script locally falls back to the key already on that machine, so
no secrets are needed to seed history by hand.

## Refreshing by hand

Actions tab, **Refresh portfolio data**, Run workflow. The `backfill_days`
input re-fetches that many days if a report was revised or missed.
