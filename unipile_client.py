"""
Minimal Unipile client for the 3 LinkedIn-facing calls this automation makes:
profile lookup, people search, and profile email fetch. Every public function
here sleeps 8-13s (randomized, a floor not a target — see DESIGN.md §5)
*before* making the live call, and round-robins across whichever
UNIPILE_ACCOUNT_IDS are configured.

Endpoints follow developer.unipile.com's documented shapes as of writing;
confirm against current docs before relying on this in production, since
Unipile's API evolves.
"""
import itertools
import random
import time

import requests

from config import (
    UNIPILE_ACCOUNT_IDS,
    UNIPILE_API_KEY,
    UNIPILE_BASE_URL,
    UNIPILE_SLEEP_MAX_SECONDS,
    UNIPILE_SLEEP_MIN_SECONDS,
)

_account_cycle = itertools.cycle(UNIPILE_ACCOUNT_IDS) if UNIPILE_ACCOUNT_IDS else None


def next_account_id():
    if not _account_cycle:
        raise RuntimeError(
            "No UNIPILE_ACCOUNT_IDS configured — set the env var to the "
            "comma-separated Unipile account_ids for Jules Resny, Jules "
            "Ferrer and Vincent Lerat before running for real."
        )
    return next(_account_cycle)


def _sleep_before_call():
    delay = random.uniform(UNIPILE_SLEEP_MIN_SECONDS, UNIPILE_SLEEP_MAX_SECONDS)
    time.sleep(delay)
    return delay


def _headers():
    return {"X-API-KEY": UNIPILE_API_KEY, "Content-Type": "application/json"}


def _is_unreachable_profile(resp):
    """True for statuses that mean 'this profile can't be fetched' rather
    than a bug in our request: 404 (not found) and 422 with Unipile's
    errors/invalid_recipient (profile locked/restricted/unreachable). Both
    are expected, non-actionable outcomes for a subset of real LinkedIn
    profiles and should be skipped, not raised as automation errors."""
    if resp.status_code == 404:
        return True
    if resp.status_code == 422:
        try:
            return resp.json().get("type") == "errors/invalid_recipient"
        except ValueError:
            return True
    return False


def get_profile(handle_or_id: str, account_id: str | None = None):
    """Fetch a LinkedIn profile by public identifier (the /in/<handle> slug)
    or Unipile provider id. Returns None on 404 (profile not found/blocked)
    or 422 invalid_recipient (profile locked/unreachable) rather than
    raising."""
    account_id = account_id or next_account_id()
    _sleep_before_call()
    resp = requests.get(
        f"{UNIPILE_BASE_URL}/api/v1/users/{handle_or_id}",
        headers=_headers(),
        params={"account_id": account_id},
        timeout=30,
    )
    if _is_unreachable_profile(resp):
        return None
    resp.raise_for_status()
    return resp.json()


def search_people(name: str, company: str | None = None, title: str | None = None,
                   account_id: str | None = None):
    """LinkedIn classic people-search. Returns the raw list of candidate
    profiles (caller applies the matching-confidence policy — see
    DESIGN.md §4, never auto-commit here)."""
    account_id = account_id or next_account_id()
    keywords = " ".join(p for p in [name, company, title] if p)
    _sleep_before_call()
    resp = requests.post(
        f"{UNIPILE_BASE_URL}/api/v1/linkedin/search",
        headers=_headers(),
        params={"account_id": account_id},
        json={"api": "classic", "category": "people", "keywords": keywords},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("items", [])


def get_email(handle_or_id: str, account_id: str | None = None):
    """Attempt to resolve an email address for a LinkedIn profile. Returns
    None if Unipile can't find one (common — not every profile exposes an
    email even with a connection) or if the profile is unreachable (404 or
    422 invalid_recipient)."""
    account_id = account_id or next_account_id()
    _sleep_before_call()
    resp = requests.get(
        f"{UNIPILE_BASE_URL}/api/v1/users/{handle_or_id}/email",
        headers=_headers(),
        params={"account_id": account_id},
        timeout=30,
    )
    if _is_unreachable_profile(resp):
        return None
    resp.raise_for_status()
    return resp.json().get("email")


def extract_location(profile: dict):
    """Best-effort city/country extraction from a Unipile profile payload.
    Unipile's field names have varied across API versions — check a couple
    of live responses when wiring this up for real and adjust."""
    if not profile:
        return None
    city = profile.get("location") or profile.get("city")
    country = profile.get("country") or profile.get("location_country")
    if not city and not country:
        return None
    return {"city": city, "country": country}
