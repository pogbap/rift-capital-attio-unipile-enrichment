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

**"Still needs work" addendum (2026-09-17).** The query above matches the *target population*, not
"who still needs enrichment" — early runs always called it with `offset=0` and nothing tracked
between runs, so every run just asked Attio for "the first N people matching personae_type" again.
Attio's own docs say an unsorted query returns "a deterministic random order," so this wasn't
pagination at all: it re-sampled whatever "first N" a live, constantly-changing workspace (real
email/calendar sync keeps adding new matching people) happened to return each time. Two concrete
symptoms: a person who's already fully enriched (`linkedin` and `primary_location` both set) could
still get pulled into a batch and waste a Unipile call for nothing, and there was no guarantee every
targeted person ever actually got visited.

Fixed in `attio_client.query_target_people()` by additionally filtering out, in Python, any record
that already has both `linkedin` and `primary_location` filled in — paginating through the
personae_type-matching population (25/50-record pages) until enough still-needing records are
collected to fill the batch, rather than trusting a single unpaginated call. This couldn't be pushed
into Attio's own filter: `$not_empty` is only documented for domain/name/phone/interaction/
record_reference/text attributes, not the composite `location` type `primary_location` is. See
`attio_client.py` for the implementation and its own test (`test_pagination.py`, not part of the
original `test_smoke.py`).

**"Already flagged" addendum (2026-09-17, same day).** The gap above wasn't hypothetical: the very
next run after shipping the fix above surfaced it immediately — "Yorkseed ™" and Stan Chanavat (the
two unresolvable examples from run #25) got a second, duplicate "Needs LinkedIn Review" Note within
hours, because they still have no `linkedin` value and so still matched "still needs work." Fixed by
adding a second condition to `query_target_people()`'s per-record skip: for any record that would go
to Branch B (no `linkedin` on file), also skip it if `has_needs_linkedin_review_note()` finds an
existing "Needs LinkedIn Review" Note already on the record. This does *not* apply to Branch A
records (have `linkedin`, still missing `primary_location`) — a stale Note from before someone got
their LinkedIn URL added by hand must never block their location refresh; see `_would_go_to_branch_b()`
and its test in `test_note_guard.py`. A human resolving the situation (adding the `linkedin` URL, or
deleting the Note to ask for a fresh look) is what clears this now — not another automated pass.
Doesn't need a new attribute/schema change; the existing Note *is* the "already flagged" signal, we
just weren't checking for it before creating another one.

Batch-size caution (2026-09-17): a manually-triggered run with `batch_size: 3000` hit GitHub Actions'
6-hour job timeout and got force-cancelled with nothing processed or logged (`run_logger.log_run()`
only writes once, at the end of the whole batch). Not a bug in the job itself — at the mandatory
8–13s-per-call pacing (§5), that batch size was never going to finish in 6 hours — but there's no
upper-bound validation on the `batch_size` workflow input today, so a typo or an overly ambitious
manual run can silently burn the entire job timeout for zero result. Worth adding an explicit cap in
`manual-enrichment.yml` if this recurs.

**Scan ceiling addendum (2026-09-17, later same day).** The two fixes above created a new failure mode
of their own: `query_target_people()` paginates from `offset=0` every run (nothing persisted between
runs) and stopped scanning once `scanned >= max_scan`, which defaulted to 500 and was never overridden
from `enrichment.py`. As more of the *front* of Attio's fixed unsorted order got fully enriched or
Branch-B-flagged over successive runs, the first 500 records eventually became 100% done/flagged —
and since every run re-scans that same first-500 window from scratch, the job started returning
`{'records_processed': 0, ...}` on every run, even though a live check confirmed thousands of
untouched records exist further down (a spot-check at offset 500 found 18/50 still missing `linkedin`
and 4/50 still missing `primary_location`). The 500-record scan window had simply never reached them.

Fixed by adding `MAX_SCAN_PER_RUN` to `config.py` (default 8000, comfortably above the ~6k target
population) and threading it through as `query_target_people(limit=..., max_scan=config.MAX_SCAN_PER_RUN)`
in `enrichment.py` — previously `max_scan` silently used the function's own default and was never wired
to anything. Raising the scan ceiling is cheap: Attio list/notes calls carry no artificial pacing (only
Unipile calls do, via §5's 8-13s floor), so scanning the whole population every run costs some extra
Attio API round trips, not extra wall-clock hours. The real per-run time budget stays governed by
`BATCH_SIZE_PER_RUN` (how many *matching* records actually get processed with Unipile calls), not by
how far the scan has to walk to find them. This is a stopgap, not a permanent fix — a workspace that
keeps growing past `MAX_SCAN_PER_RUN` before this job manages to enrich its backlog down would hit the
exact same wall again at a higher record count; the durable fix would be server-side filtering (once
Attio's `$not_empty` supports the `location` type) or persisting scan offset/state between runs instead
of always restarting at 0.

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

This stays strict for identity: the `linkedin` field is never written on a guess, no matter how the
corroborating signal was produced (free-text or structured, see below). Only the **location** policy
was relaxed on 2026-09-14 — see the note at the end of this section.

**Company corroboration (2026-09-16 — closes a gap left open at first build).** The original
`_company_corroborates()` was stubbed to always return `False` ("left permissive... until wired to a
real company-name lookup, so it never silently over-claims a match on company alone" — see the
original commit). That meant a strong match could in practice only come from title corroboration,
a strict substring check against the LinkedIn headline — brittle, and the main driver of the
`flagged_for_review` volume observed in early runs (run #22: 8 of 20 records flagged, mostly for
"no corroborating company/title signal"). Two layers now feed corroboration, cheapest first:

1. **Free-text company-name match** — `_company_corroborates()` now actually checks whether the
   person's known employer (pulled from their linked Attio company record) appears in the
   candidate's LinkedIn headline, the same technique already used for title. No new external calls:
   the company name comes from a single existing Attio hop, and the check just wasn't wired up in
   the initial build.
2. **Structured company-id disambiguation (Jules Ferrer / Sales Navigator only)** — when neither
   company-name-in-headline nor title corroboration produces a single strong match, `enrichment.py`
   makes one more attempt before flagging: `_disambiguate_via_sales_nav()` re-runs the people search
   on **Jules Ferrer's account specifically** (the one Sales-Navigator-enabled seat among the three
   connected LinkedIn accounts), filtered by the person's employer as LinkedIn's own *structured*
   "current company" field (`company: [<linkedin_company_id>]`) rather than free-text. The company id
   is resolved two ways, cheapest/most-precise first:
   - if the Attio company record already has a `linkedin` URL on file, extract the page slug and
     resolve it directly via `GET /linkedin/company/<slug>` — exact by construction, no ambiguity;
   - otherwise, fall back to a LinkedIn company-name search (`search_company_id()`), only trusting a
     single exact (case-insensitive) name match.

   A single confident name match against that company-filtered candidate list is trusted as a strong
   match — it's LinkedIn's own structured data confirming the employer, not a guess — and gets
   written exactly like an ordinary strong match. Live-tested against real flagged records before
   shipping: resolved a real ambiguous case (Mark Chan / Happiness Capital: free-text search returned
   Mark Chan plus lookalikes with no corroborating headline text; the company-filtered search on
   Jules Ferrer's account narrowed it to exactly one confident match). Also confirmed this **doesn't
   always resolve** a case — some people's LinkedIn "Experience" section isn't tagged to their
   employer's official Company Page, so the structured filter can legitimately come back empty; when
   that happens the record still falls through to the normal flagging path, with a note added to the
   review reason so a human knows a company-verified check was already tried and came up empty.

   This is best-effort and additive only: no `UNIPILE_SALES_NAV_ACCOUNT_ID` configured, an
   unresolvable company, or any lookup error along the way all just fall back to flagging as before
   — none of it is allowed to raise into `run_stats["errors"]` (see §5/the unipile_client fix of
   2026-09-16 for why a single optional-enhancement failure must never fail the whole run). It also
   costs up to 2 extra Unipile calls (company lookup + company-filtered people search), spent only on
   the subset of records that would otherwise be flagged, and only against Jules Ferrer's account
   rather than the round-robin — worth watching in `runs.log.jsonl` (`company_verified_matches`) if
   that account's daily volume becomes a concern (see §6's per-account envelope).

**Location policy (2026-09-14, unchanged by the above).** Location is handled separately and more
permissively than identity: locations matter more than 100%-clean LinkedIn matches for this
workspace, so even a flagged/ambiguous record still gets a best-effort location written from the top
search candidate when the person's `primary_location` is empty. Worst case on a wrong candidate is an
imprecise location on an already-flagged record (visible in the same review note) — never a wrong
LinkedIn URL, which stays fully gated by everything above.

## 5. Pacing (non-negotiable, every Unipile call)

Every LinkedIn-facing Unipile call (profile fetch, people search, company lookup/search, email
fetch) is preceded by a `random.uniform(8, 13)` second sleep — a floor, not a target. Unipile's own
"Provider Limits and Restrictions" guidance (`developer.unipile.com/docs/provider-limits-and-restrictions`)
says: space calls out with random delays, don't chain them at fixed intervals, start conservative on
newer/lower-volume accounts, and prefer webhooks to polling where possible.

Their documented safe envelope for a standard (non-Sales-Navigator) LinkedIn account:
- ~100 profile views/day (recommended, not hard-enforced)
- most other discrete actions (searches, etc.) bucketed at ~100/day/account

A profile/page fetch or search that comes back 404 (not found) or 422 `errors/invalid_recipient`
(locked/restricted/unreachable) is treated as a normal "nothing here" outcome, not an automation
error — see `unipile_client._is_unreachable_profile()` (added 2026-09-16 after run #20 failed the
whole batch over exactly one such profile).

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

Jules Ferrer's account carries extra load beyond its round-robin share: it's also the only account
used for the §4 Sales Navigator disambiguation, up to 2 extra calls per record that would otherwise
be flagged (roughly 40% of Branch B in early runs). Still comfortably inside the envelope at current
batch sizes, but re-check this account's daily total specifically if batch size or cadence is ever
increased.

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
  `UNIPILE_SALES_NAV_ACCOUNT_ID` (§4) is a separate, optional env var — set it to Jules Ferrer's
  account_id specifically (already one of the values in `UNIPILE_ACCOUNT_IDS`) to enable the
  disambiguation step; leave unset to disable it, no other behavior changes.
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
- Watch `company_verified_matches` vs `flagged_for_review` in `runs.log.jsonl` for a couple of weeks
  to see how much the §4 Sales Navigator disambiguation is actually cutting the review queue, and
  whether Jules Ferrer's account needs its own, smaller batch-size allowance as a result.
