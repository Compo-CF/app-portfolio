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
