"""
Minimal Unipile client for the LinkedIn-facing calls this automation makes:
profile lookup, people search, company lookup/search, and profile email
fetch. Every public function here sleeps 8-13s (randomized, a floor not a
target — see DESIGN.md §5) *before* making the live call, and round-robins
across whichever UNIPILE_ACCOUNT_IDS are configured, unless an explicit
account_id is passed in (used for the Jules Ferrer / Sales Navigator
disambiguation path — see DESIGN.md §4).

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
    """True for statuses that mean 'this profile/page can't be fetched'
    rather than a bug in our request: 404 (not found) and 422 with
    Unipile's errors/invalid_recipient (locked/restricted/unreachable).
    Both are expected, non-actionable outcomes for a subset of real
    LinkedIn profiles/pages and should be skipped, not raised as
    automation errors."""
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
                   account_id: str | None = None, company_id: str | None = None):
    """LinkedIn classic people-search. Returns the raw list of candidate
    profiles (caller applies the matching-confidence policy — see
    DESIGN.md §4, never auto-commit here).

    `company_id` is an optional structured "current company" filter — a
    LinkedIn company id, typically from get_company_id_by_slug() or
    search_company_id() — which narrows results using LinkedIn's own
    structured data on the candidate, rather than matching a company name
    against free-text headline. Only pass a `name` keyword alongside it
    (leave `company`/`title` as text out) so the structured filter does
    the narrowing, not another round of free-text guessing."""
    account_id = account_id or next_account_id()
    keywords = " ".join(p for p in [name, company, title] if p)
    payload = {"api": "classic", "category": "people", "keywords": keywords}
    if company_id:
        payload["company"] = [company_id]
    _sleep_before_call()
    resp = requests.post(
        f"{UNIPILE_BASE_URL}/api/v1/linkedin/search",
        headers=_headers(),
        params={"account_id": account_id},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("items", [])


def get_company_id_by_slug(slug: str, account_id: str | None = None):
    """Resolve a LinkedIn company page slug (e.g. 'happinesscapital', from
    a company's own profile_url/linkedin field already on file in Attio)
    to Unipile's numeric company id, via a direct company-profile fetch.
    Exact by construction — no name-matching ambiguity, unlike
    search_company_id(). Returns None if the page can't be fetched (404 or
    422, same reasoning as _is_unreachable_profile)."""
    account_id = account_id or next_account_id()
    _sleep_before_call()
    resp = requests.get(
        f"{UNIPILE_BASE_URL}/api/v1/linkedin/company/{slug}",
        headers=_headers(),
        params={"account_id": account_id},
        timeout=30,
    )
    if _is_unreachable_profile(resp):
        return None
    resp.raise_for_status()
    return resp.json().get("id")


def search_company_id(name: str, account_id: str | None = None):
    """Fallback for when no company LinkedIn page is on file: resolve a
    company name to its LinkedIn company id via LinkedIn's own company
    search. Only trusts an exact (case-insensitive) single name match —
    with zero or multiple/fuzzy candidates, returns None rather than risk
    corroborating against the wrong company."""
    account_id = account_id or next_account_id()
    _sleep_before_call()
    resp = requests.post(
        f"{UNIPILE_BASE_URL}/api/v1/linkedin/search",
        headers=_headers(),
        params={"account_id": account_id},
        json={"api": "classic", "category": "companies", "keywords": name},
        timeout=30,
    )
    resp.raise_for_status()
    candidates = resp.json().get("items", [])
    exact = [c for c in candidates if (c.get("name") or "").strip().lower() == name.strip().lower()]
    return exact[0].get("id") if len(exact) == 1 else None


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
