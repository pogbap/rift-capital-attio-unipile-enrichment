# Attio People Enrichment via Unipile — Design & Decisions

Redesign of the retired Make scenario `7303123_Attio_Location_Enrichment_via_Unipile.json`
(0 executions, several unresolved issues — see original build prompt). This document
records every decision needed to build it, plus what's still outstanding.

## 1. Target population — Attio filter

Confirmed against the live workspace (`mcp__Attio__list-attribute-definitions` on `people`):

- Attribute: `personae_type` (displayed as "ICP" in the UI), `type: select`, `is_multiselect: true`.
- The 7 target option titles all exist today: `AF`, `FO / MFO`, `HNWI / gros LP direct`, `GP`,
  `Distributeur`, `CGP`, `Club`. (The workspace now also has `Broker`, `Syndicate`, `Interne`,
  `Others`, `lemwarm`, `Support`, `Management`, `KO`, `Event` — not targeted.)

Attio's REST filtering docs (`docs.attio.com/rest-api/guides/filtering-and-sorting`) support `$in`
for select-type attributes — this replaces the source scenario's 7 OR'd `$eq` conditions:

```json
{
  "filter": {
    "personae_type": {
      "$in": ["AF", "FO / MFO", "HNWI / gros LP direct", "GP", "Distributeur", "CGP", "Club"]
    }
  }
}
```

Filtering by option **title** (not option ID) works directly against `/v2/objects/people/records/query`.
Option IDs are recorded in `config.py` as a fallback in case a workspace migration ever requires
filtering by ID instead of label.

## 2. `primary_location` — structured value shape

`primary_location` is `type: location` (confirmed in schema, `is_writable: true`). Per Attio's
location attribute docs, a write must supply **all** of these keys (use `null` for anything unknown
— partial objects are rejected):

```json
{
  "line_1": null, "line_2": null, "line_3": null, "line_4": null,
  "locality": "London", "region": null, "postcode": null,
  "country_code": "GB", "latitude": null, "longitude": null
}
```

`country_code` must be ISO 3166-1 alpha-2. Unipile returns free-text city/country strings, so
`map_location()` in `enrichment.py` does best-effort city → `locality` and country name → alpha-2
code (small lookup table + fallback to leaving `country_code` null rather than guessing wrong).

## 3. Overwrite policy — **confirmed with user: fill-if-empty only**

The job never touches a person whose `primary_location` already has any value. It only writes when
the attribute is currently null/empty. (User confirmed this over the "always overwrite" alternative.)

## 4. Branch B matching policy (highest-risk step)

- Strong match = name match **and** at least one of {company, job title} corroborating → auto-write
  `linkedin` and proceed to location lookup.
- Anything else (multiple plausible candidates, or a weak/partial name-only match) → **do not write
  `linkedin` or `primary_location`**. Instead, flag for human review by creating an Attio **Note** on
  the person record (via `mcp__Attio__create-note`) titled `Needs LinkedIn Review`, listing the
  candidate profile(s) found and why confidence was insufficient. No new Attio attribute/schema
  change is required for this — a Note is enough to surface it to the team; can be upgraded to a
  dedicated status/tag attribute later if the team wants it filterable as a view.

## 5. Pacing (non-negotiable, every Unipile call)

Every LinkedIn-facing Unipile call (profile fetch, people search, email fetch) is preceded by a
`random.uniform(8, 13)` second sleep — a floor, not a target. Unipile's own "Provider Limits and
Restrictions" guidance (`developer.unipile.com/docs/provider-limits-and-restrictions`) says: space
calls out with random delays, don't chain them at fixed intervals, start conservative on
newer/lower-volume accounts, and prefer webhooks to polling where possible.

Their documented safe envelope for a standard (non-Sales-Navigator) LinkedIn account:
- ~100 profile views/day (recommended, not hard-enforced)
- most other discrete actions (searches, etc.) bucketed at ~100/day/account

## 6. Batch size & cadence — re-derived, not copied from source

Source did 5 records/run × every 15 min × 9h weekday window (36 runs/day = up to 180 Branch-A-only
lookups/day) — already over Unipile's own safe envelope for a *single* action type, before Branch B's
extra people-search call is added.

**Key change: three Unipile-connected LinkedIn accounts exist** (Jules Resny, Jules Ferrer, Vincent
Lerat — user-confirmed, all already connected in Unipile), not one. The job round-robins across all
three `account_id`s, which roughly triples the safe daily envelope versus a single-account design.

Recommended: **3 records per run, every 30 minutes, weekdays 09:00–18:00 Europe/Paris** (~18 runs/day
→ ≤54 records/day). Each record costs 1 Unipile call (Branch A) or 2–3 (Branch B: search, optional
follow-up profile fetch). Spread across 3 accounts round-robin, worst case ≈ 45 calls/account/day —
comfortably under the ~100/day/account envelope even before accounting for the review-flag path,
which stops after the search call and never reaches a second Unipile call.

This is a starting point, intentionally conservative — tighten or loosen after watching the run log
for a couple of weeks. Rather than guessing further, treat cadence as tunable in `config.py`.

## 7. Run logging

Each run appends one record to Attio... no — per the brief, logs go to a durable store outside
Attio itself. Implemented as an Attio-independent JSON Lines file (`runs.log.jsonl`) written next to
the script, one line per run: `{timestamp, records_processed, written, flagged_for_review, errors}`.
A Notion "Automation Runs" database is the natural upgrade (the Notion connector is already
available to this account) — `notion_logger.py` is stubbed with the `notion-create-pages` call
commented in, ready to enable once a destination page/database is picked.

## 8. On-demand email → Lemlist handoff (`lemlist_handoff.py`)

Confirmed with user: this step is about **finding** an email via Unipile when Attio doesn't have
one — not about auto-enrolling someone in an outreach sequence. Two hard gates, both required:

1. Only ever run manually, per-contact (never from the scheduled batch job).
2. The actual Lemlist push only fires after an explicit human confirmation
   (`--confirm-push` flag) — by default the script fetches the email, writes it to Attio's
   `email_addresses` if not already present, and then **prints the intended Lemlist payload and
   stops**, so a person reviews it before anything is sent.

Lemlist is not provisioned yet (confirmed with user — "not ready, stub it"). `lemlist_client.py`
raises `LemlistNotConfigured` until `LEMLIST_API_KEY` and `LEMLIST_CAMPAIGN_ID` are set — the rest
of the flow (email fetch + Attio write) works today without it.

## 9. Credentials

- **Unipile**: three LinkedIn accounts already connected in Unipile (Jules Resny, Jules Ferrer,
  Vincent Lerat). Script expects `UNIPILE_API_KEY` and `UNIPILE_ACCOUNT_IDS` (comma-separated,
  round-robin) as environment variables — actual values still need to be pulled from the Unipile
  dashboard and set wherever this script runs (not hardcoded in code or in a scheduled-task prompt).
- **Attio**: this session already has a live Attio connection (used for all the schema lookups
  above) — the scheduled/on-demand runs can either reuse that MCP connection (if run as a Claude
  scheduled task) or use a raw `ATTIO_API_KEY` (script supports both call styles, see `attio_client.py`).
- **Lemlist**: not yet provisioned — `LEMLIST_API_KEY` / `LEMLIST_CAMPAIGN_ID` placeholders only.

## 10. Still outstanding

- Real values for `UNIPILE_API_KEY` / `UNIPILE_ACCOUNT_IDS` (exist in Unipile, need to be copied in).
- Lemlist account + API key + target campaign/list.
- Decide script hosting: a Claude scheduled task can run this by shelling out to the script if the
  workspace persists between firings; otherwise run it from wherever Rift Capital already hosts
  small automations (a cron box, a serverless function) and point *that* at Attio + Unipile.
