# Attio People Enrichment via Unipile

Read `DESIGN.md` first -- it records every decision this was built against
(Attio filter syntax, the `primary_location` value shape, the Branch B
matching policy, pacing, cadence, and what's still outstanding).

## Setup

```bash
pip install -r requirements.txt
export ATTIO_API_KEY=...
export UNIPILE_API_KEY=...
export UNIPILE_ACCOUNT_IDS=jules_resny_id,jules_ferrer_id,vincent_lerat_id
```

`UNIPILE_ACCOUNT_IDS` and the Lemlist variables are still placeholders --
see DESIGN.md §10 for what's outstanding before this runs against real data.

## Scheduled batch job (LinkedIn URL + location enrichment)

```bash
python enrichment.py
```

One invocation = one run = one batch (default 3 records, `config.py`).
Schedule it externally -- every 30 min, weekdays 09:00-18:00 Europe/Paris is
the recommended starting cadence (DESIGN.md §6). Every run appends a line to
`runs.log.jsonl`.

Never writes to Lemlist. Never overwrites an existing `primary_location`.
Never guesses on an ambiguous LinkedIn match -- it leaves an Attio Note
("Needs LinkedIn Review") instead.

## On-demand: email -> Lemlist handoff

Manually triggered, one contact at a time -- never run from the scheduler.

```bash
python lemlist_handoff.py <attio_record_id>              # dry run (default)
python lemlist_handoff.py <attio_record_id> --confirm-push # actually pushes
```

Dry run finds/writes the email to Attio and prints the Lemlist payload it
*would* send, then stops. `--confirm-push` is required to actually push --
and will fail with `LemlistNotConfigured` until real Lemlist credentials
are set, since that account doesn't exist yet.
