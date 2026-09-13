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


def process_branch_b(person, run_stats):
    """No linkedin URL on file — search, and only commit a confident match."""
    candidates = unipile_client.search_people(
        name=person["name"], company=None, title=person["job_title"]
    )

    strong_matches = [
        c for c in candidates
        if _name_matches(c, person["name"]) and (
            _company_corroborates(c, person) or _title_corroborates(c, person)
        )
    ]

    if len(strong_matches) == 1:
        match = strong_matches[0]
    elif len(candidates) == 1 and _name_matches(candidates[0], person["name"]):
        # single result, name matches, but no corroborating signal available
        # (e.g. company/title unknown) -- still ambiguous per policy, flag it
        match = None
        attio_client.flag_needs_linkedin_review(
            person["id"],
            reason="Single candidate but no corroborating company/title signal",
            candidates=candidates,
        )
        run_stats["flagged_for_review"] += 1
        return
    else:
        match = None

    if match is None:
        attio_client.flag_needs_linkedin_review(
            person["id"],
            reason="Multiple plausible candidates or weak/partial match" if candidates else "No candidates found",
            candidates=candidates,
        )
        run_stats["flagged_for_review"] += 1
        return

    linkedin_url = match.get("public_profile_url") or match.get("profile_url")
    if not linkedin_url:
        run_stats["errors"].append(f"{person['id']}: matched candidate had no profile URL")
        return

    attio_client.update_person(person["id"], {"linkedin": linkedin_url})
    run_stats["linkedin_written"] += 1

    # Prefer location already present on the search result; otherwise a
    # fresh profile fetch (own pacing sleep applied inside the client).
    loc = unipile_client.extract_location(match)
    if not loc:
        handle = extract_handle(linkedin_url)
        if handle:
            profile = unipile_client.get_profile(handle)
            loc = unipile_client.extract_location(profile)
    if loc:
        write_location_if_allowed(person, loc["city"], loc["country"], run_stats)


def _name_matches(candidate: dict, name: str) -> bool:
    if not name:
        return False
    cand_name = (candidate.get("name") or "").strip().lower()
    return cand_name == name.strip().lower()


def _company_corroborates(candidate: dict, person: dict) -> bool:
    # Best-effort: requires the search response to include a current company
    # name/id to compare against person["company_record_id"]. Left permissive
    # (returns False) until wired to a real company-name lookup, so it never
    # silently over-claims a match on company alone.
    return False


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
        "skipped_location_not_empty": 0,
        "errors": [],
    }

    records = attio_client.query_target_people(limit=config.BATCH_SIZE_PER_RUN)
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
