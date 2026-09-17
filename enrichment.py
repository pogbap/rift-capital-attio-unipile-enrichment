"""
Scheduled batch job: Attio People -> LinkedIn URL + location enrichment via
Unipile. Run this on a schedule (see DESIGN.md §6 for recommended cadence:
3 records / run, every 30 min, weekdays 09:00-18:00 Europe/Paris) rather
than looping continuously — one process invocation == one run == one batch.

python enrichment.py

Never pushes anything to Lemlist — that's a separate, manually-triggered
script (lemlist_handoff.py), by design (see DESIGN.md §8).
"""
import re
import sys

import attio_client
import config
import location_map
import run_logger
import unipile_client

LINKEDIN_HANDLE_RE = re.compile(r"/in/([^/?#]+)")
COMPANY_SLUG_RE = re.compile(r"/company/([^/?#]+)")


def extract_handle(linkedin_url: str) -> str | None:
    m = LINKEDIN_HANDLE_RE.search(linkedin_url or "")
    return m.group(1) if m else None


def has_value(attio_value) -> bool:
    """Attio 'empty' can be None, [], or a values-list with no entries."""
    if not attio_value:
        return False
    if isinstance(attio_value, list) and len(attio_value) == 0:
        return False
    return True


def write_location_if_allowed(person, city, country, run_stats):
    if not city and not country:
        return
    if has_value(person["primary_location"]) and not config.OVERWRITE_EXISTING_LOCATION:
        run_stats["skipped_location_not_empty"] += 1
        return
    attio_client.update_person(person["id"], {"primary_location": location_map.to_attio_location(city, country)})
    run_stats["locations_written"] += 1


def process_branch_a(person, run_stats):
    """Person already has a linkedin URL — just refresh location."""
    handle = extract_handle(person["linkedin_url"])
    if not handle:
        run_stats["errors"].append(f"{person['id']}: linkedin URL present but no /in/ handle found")
        return
    profile = unipile_client.get_profile(handle)
    loc = unipile_client.extract_location(profile)
    if loc:
        write_location_if_allowed(person, loc["city"], loc["country"], run_stats)


def _write_location_from_candidate(person, candidate, run_stats):
    """Best-effort location extraction + write from a Unipile search
    candidate (or a matched profile). Prefers location fields already on the
    candidate; falls back to a full profile fetch (own pacing sleep applied
    inside the client) when the search result doesn't carry them. Never
    touches the linkedin field — that stays gated by the identity-confidence
    policy in process_branch_b."""
    if not candidate:
        return
    loc = unipile_client.extract_location(candidate)
    if not loc:
        linkedin_url = candidate.get("public_profile_url") or candidate.get("profile_url")
        handle = extract_handle(linkedin_url or "")
        if handle:
            profile = unipile_client.get_profile(handle)
            loc = unipile_client.extract_location(profile)
    if loc:
        write_location_if_allowed(person, loc["city"], loc["country"], run_stats)


def process_branch_b(person, run_stats):
    """No linkedin URL on file — search Unipile.

    LinkedIn identity writes stay strict: only a confident single match ever
    gets written to the `linkedin` field, everything else is flagged via an
    Attio Note for a human to confirm — never guessed.

    Location is handled separately and more permissively (explicit product
    decision, 2026-09-14: locations matter more than 100%-clean LinkedIn
    matches for this workspace). Even when the identity match is too
    ambiguous to write, we still take a best-effort location from the top
    search candidate and write it if the person's primary_location is empty.
    Worst case on a wrong candidate is an imprecise location on an
    already-flagged record (visible in the same review note) — never a wrong
    LinkedIn URL, which stays fully gated.

    Addition (2026-09-16): when the free-text search alone can't produce a
    strong match, this now makes one more attempt before flagging --
    _disambiguate_via_sales_nav() re-runs the search on Jules Ferrer's
    Sales-Navigator-enabled Unipile account, filtered by the person's known
    employer as LinkedIn's own *structured* "current company" field
    (resolved from the company's LinkedIn page already on file in Attio, or
    a company-name search as fallback) instead of guessing from headline
    text. A single confident name match against that narrowed list is
    trusted as a strong match, same as a normal strong match above -- it's
    LinkedIn's own structured data corroborating the identity, not our
    guess. This is best-effort only: no Sales Nav account configured, an
    unresolvable company, or any lookup error all just fall back to
    flagging as before, and never raise into run_stats['errors'] (an
    optional enhancement failing should never fail the whole run over it --
    see the 2026-09-16 unipile_client fix for exactly this failure mode).
    """
    company_info = None
    if person.get("company_record_id"):
        try:
            company_info = attio_client.get_company_info(person["company_record_id"])
        except Exception:
            company_info = None  # best-effort only -- falls back to title-only corroboration

    company_name = (company_info or {}).get("name")

    candidates = unipile_client.search_people(
        name=person["name"], company=None, title=person["job_title"]
    )

    strong_matches = [
        c for c in candidates
        if _name_matches(c, person["name"]) and (
            _company_corroborates(c, company_name) or _title_corroborates(c, person)
        )
    ]

    if len(strong_matches) == 1:
        match = strong_matches[0]
        reason = None
    elif len(candidates) == 1 and _name_matches(candidates[0], person["name"]):
        # single result, name matches, but no corroborating signal available
        # (e.g. company/title unknown) -- still ambiguous per policy, flag it
        match = None
        reason = "Single candidate but no corroborating company/title signal"
    else:
        match = None
        reason = "Multiple plausible candidates or weak/partial match" if candidates else "No candidates found"

    if match is None and company_info:
        match, reason = _disambiguate_via_sales_nav(person, company_info, reason, run_stats)

    if match is None:
        attio_client.flag_needs_linkedin_review(person["id"], reason=reason, candidates=candidates)
        run_stats["flagged_for_review"] += 1
        # Still take a best-effort location from the top candidate -- see
        # docstring above for why this is treated as lower-risk than the
        # LinkedIn URL itself.
        if candidates:
            _write_location_from_candidate(person, candidates[0], run_stats)
        return

    linkedin_url = match.get("public_profile_url") or match.get("profile_url")
    if not linkedin_url:
        run_stats["errors"].append(f"{person['id']}: matched candidate had no profile URL")
        return

    attio_client.update_person(person["id"], {"linkedin": linkedin_url})
    run_stats["linkedin_written"] += 1

    _write_location_from_candidate(person, match, run_stats)


def _disambiguate_via_sales_nav(person, company_info, reason, run_stats):
    """Last resort before flagging -- see process_branch_b docstring.
    Returns (match_or_None, updated_reason). Never raises: any failure here
    is folded into the flag reason instead, so an optional enhancement can
    never turn into a whole-run failure."""
    account_id = config.UNIPILE_SALES_NAV_ACCOUNT_ID
    if not account_id:
        return None, reason
    try:
        company_id = _resolve_company_linkedin_id(company_info, account_id)
        if not company_id:
            return None, f"{reason} (employer '{company_info.get('name')}' not confidently resolved on LinkedIn)"
        narrowed = unipile_client.search_people(name=person["name"], account_id=account_id, company_id=company_id)
        strong = [c for c in narrowed if _name_matches(c, person["name"])]
        if len(strong) == 1:
            run_stats["company_verified_matches"] += 1
            return strong[0], None
        return None, (
            f"{reason}; company-filtered search at {company_info.get('name')} found "
            f"{len(strong)} confident match(es) among {len(narrowed)} employee result(s)"
        )
    except Exception as exc:  # noqa: BLE001 - best-effort disambiguation only
        return None, f"{reason} (company-verified lookup failed: {exc})"


def _resolve_company_linkedin_id(company_info, account_id):
    """Prefer the company's own LinkedIn page (exact, no ambiguity) over a
    free-text name search (fallback for when Attio has no linkedin field
    on the company record)."""
    linkedin_url = company_info.get("linkedin_url")
    if linkedin_url:
        m = COMPANY_SLUG_RE.search(linkedin_url)
        if m:
            company_id = unipile_client.get_company_id_by_slug(m.group(1), account_id=account_id)
            if company_id:
                return company_id
    name = company_info.get("name")
    return unipile_client.search_company_id(name, account_id=account_id) if name else None


def _name_matches(candidate: dict, name: str) -> bool:
    if not name:
        return False
    cand_name = (candidate.get("name") or "").strip().lower()
    return cand_name == name.strip().lower()


def _company_corroborates(candidate: dict, company_name: str | None) -> bool:
    """Free-text check: does the person's known employer name show up in
    the candidate's LinkedIn headline? A miss here isn't fatal -- process_
    branch_b falls through to the stronger structured company-id lookup
    (_disambiguate_via_sales_nav) before giving up and flagging. Never
    fuzzy -- a real substring match only, so it never over-claims."""
    if not company_name:
        return False
    return company_name.strip().lower() in (candidate.get("headline") or "").strip().lower()


def _title_corroborates(candidate: dict, person: dict) -> bool:
    cand_headline = (candidate.get("headline") or "").strip().lower()
    title = (person.get("job_title") or "").strip().lower()
    return bool(title) and title in cand_headline


def run_batch():
    run_stats = {
        "records_processed": 0,
        "linkedin_written": 0,
        "locations_written": 0,
        "flagged_for_review": 0,
        "company_verified_matches": 0,
        "skipped_location_not_empty": 0,
        "errors": [],
    }

    records = attio_client.query_target_people(
        limit=config.BATCH_SIZE_PER_RUN, max_scan=config.MAX_SCAN_PER_RUN
    )
    for raw in records:
        person = attio_client.get_record_values(raw)
        run_stats["records_processed"] += 1
        try:
            if has_value(person["linkedin_url"]):
                process_branch_a(person, run_stats)
            else:
                process_branch_b(person, run_stats)
        except Exception as exc:  # noqa: BLE001 - log and keep going
            run_stats["errors"].append(f"{person['id']}: {exc}")

    run_logger.log_run(run_stats)
    return run_stats


if __name__ == "__main__":
    stats = run_batch()
    print(stats)
    sys.exit(1 if stats["errors"] else 0)
