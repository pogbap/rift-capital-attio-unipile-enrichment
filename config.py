"""
Configuration for the Attio <-> Unipile enrichment job.

Everything secret is read from the environment — nothing is hardcoded here.
Set these wherever this script actually runs (server env, secrets manager,
scheduled-task runner config) — never paste raw keys into a scheduled task
prompt or commit them to a repo.
"""
import os

# --- Attio -------------------------------------------------------------
ATTIO_API_KEY = os.environ.get("ATTIO_API_KEY", "")
ATTIO_BASE_URL = "https://api.attio.com/v2"
ATTIO_PEOPLE_OBJECT = "people"

# The 7 personae_type option titles this automation targets. Filtering is
# done by title via Attio's $in operator; option IDs are kept here only as
# a documented fallback if a future workspace change requires filtering by
# option ID instead of label.
TARGET_PERSONAE_TYPES = [
    "AF",
    "FO / MFO",
    "HNWI / gros LP direct",
    "GP",
    "Distributeur",
    "CGP",
    "Club",
]
TARGET_PERSONAE_TYPE_OPTION_IDS = {
    "AF": "6c1e6604-7aee-489d-a766-119a836dabe0",
    "FO / MFO": "799cc574-d5ce-4331-a847-871d683dfe8b",
    "HNWI / gros LP direct": "a39b9d79-3195-4db4-a598-3792b0da674e",
    "GP": "54b96e9e-21bb-4834-afc9-7bda4925ef50",
    "Distributeur": "9484900f-9177-4a6f-afd6-6784b649b2d5",
    "CGP": "84140e43-2858-41cf-9351-3cf3b30af5eb",
    "Club": "20690565-4a04-410d-8aa1-2e7cbb8c14b1",
}

# --- Unipile -------------------------------------------------------------
UNIPILE_API_KEY = os.environ.get("UNIPILE_API_KEY", "")
UNIPILE_BASE_URL = os.environ.get("UNIPILE_BASE_URL", "https://api.unipile.com")

# Three LinkedIn-connected accounts confirmed available in Unipile
# (Jules Resny, Jules Ferrer, Vincent Lerat). The job round-robins across
# whichever account_ids are actually set here — fill in the real Unipile
# account_id values (from the Unipile dashboard) before running for real.
UNIPILE_ACCOUNT_IDS = [
    a.strip()
    for a in os.environ.get("UNIPILE_ACCOUNT_IDS", "").split(",")
    if a.strip()
]
# e.g. UNIPILE_ACCOUNT_IDS=jules_resny_account_id,jules_ferrer_account_id,vincent_lerat_account_id

# Jules Ferrer's account specifically -- the one Sales Navigator seat among
# the three above. Not part of the round-robin: used only as a targeted,
# best-effort disambiguation step in Branch B (see DESIGN.md §4) when the
# ordinary free-text search can't produce a confident match, to try a
# structured "current company" filter instead of guessing from headline
# text. Leave unset to disable the feature entirely (falls back to flagging
# for review as before, exactly like before this was added).
UNIPILE_SALES_NAV_ACCOUNT_ID = os.environ.get("UNIPILE_SALES_NAV_ACCOUNT_ID", "")

# --- Pacing (non-negotiable floor, see DESIGN.md §5) ---------------------
UNIPILE_SLEEP_MIN_SECONDS = 8
UNIPILE_SLEEP_MAX_SECONDS = 13

# --- Batching & cadence (see DESIGN.md §6; tune after watching runs.log) -
BATCH_SIZE_PER_RUN = 3

# How many raw Attio records query_target_people() will look at per run before
# giving up short of BATCH_SIZE_PER_RUN collected (see DESIGN.md §1, "Scan
# ceiling" addendum, 2026-09-17). Must comfortably exceed the target
# population size (~6k as of writing) -- every run starts scanning from
# offset 0 again (no state persisted between runs), so once the first
# max_scan records in Attio's fixed unsorted order are all done/flagged,
# a too-small cap here makes the job permanently return zero forever, no
# matter how many untouched records sit further down. Scanning itself has
# no artificial pacing (that's only on Unipile calls), so a high cap here
# is cheap -- the real time cost stays bounded by BATCH_SIZE_PER_RUN.
MAX_SCAN_PER_RUN = 8000
RUN_INTERVAL_MINUTES = 30
WORK_DAYS = {0, 1, 2, 3, 4}  # Mon-Fri
WORK_HOURS_LOCAL = (9, 18)  # Europe/Paris

# --- Overwrite policy (confirmed with user: fill-if-empty only) ---------
OVERWRITE_EXISTING_LOCATION = False

# --- Logging --------------------------------------------------------------
RUN_LOG_PATH = os.environ.get("RUN_LOG_PATH", "runs.log.jsonl")

# --- Lemlist (not provisioned yet — see DESIGN.md §8) --------------------
LEMLIST_API_KEY = os.environ.get("LEMLIST_API_KEY", "")
LEMLIST_CAMPAIGN_ID = os.environ.get("LEMLIST_CAMPAIGN_ID", "")
