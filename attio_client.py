"""
Minimal Attio REST client — just what this automation needs.
Uses a raw ATTIO_API_KEY (Bearer token). If this job instead runs inside a
Claude scheduled task that already has the Attio MCP connection, swap these
calls for the equivalent mcp__Attio__* tool calls instead (list-records /
update-record / create-note) — the query/payload shapes are the same idea.
"""
import requests

from config import ATTIO_API_KEY, ATTIO_BASE_URL, ATTIO_PEOPLE_OBJECT, TARGET_PERSONAE_TYPES


def _headers():
    return {
        "Authorization": f"Bearer {ATTIO_API_KEY}",
        "Content-Type": "application/json",
    }


def _still_needs_enrichment(record) -> bool:
    """True unless this record already has both `linkedin` and
    `primary_location` filled in -- i.e. there's nothing left for this job
    to do for them. See query_target_people() for why this check happens
    in Python rather than as part of the server-side filter."""
    values = record.get("values", {})

    def first_value(attr):
        entries = values.get(attr) or []
        return (entries[0] or {}).get("value") if entries else None

    has_linkedin = bool(first_value("linkedin"))
    has_location = bool(values.get("primary_location"))  # location entries carry no "value" key
    return not (has_linkedin and has_location)


def _fetch_people_page(page_size, offset):
    body = {
        "filter": {"$or": [{"personae_type": {"$eq": title}} for title in TARGET_PERSONAE_TYPES]},
        "limit": page_size,
        "offset": offset,
    }
    resp = requests.post(
        f"{ATTIO_BASE_URL}/objects/{ATTIO_PEOPLE_OBJECT}/records/query",
        headers=_headers(),
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def query_target_people(limit=50, max_scan=500, page_size=50):
    """
    People whose personae_type (multiselect) contains at least one of the
    target option titles, AND who still need this job's work -- i.e. don't
    already have both `linkedin` and `primary_location` filled in.

    NOTE: Attio's REST filtering does not support $in on select-type
    attributes (confirmed against the live API -- a $in filter here returns
    400 Bad Request even though $in works for text/record-reference
    attributes). Use an $or of per-title $eq conditions instead -- this is
    the same shape as the original Make scenario's 7 OR'd $eq conditions
    that DESIGN.md §1 describes replacing, restored here because $in turned
    out not to be viable for this attribute type.

    The "still needs work" half can't be folded into that same server-side
    filter: Attio's $not_empty operator is only documented for domain,
    (personal) name, phone number, interaction, record_reference and text
    attributes -- not the composite `location` type that `primary_location`
    is. So instead of one filtered query, this paginates through the
    personae_type-matching population (in whatever order Attio returns --
    "a deterministic random order" per Attio's own docs when no sort is
    given) and skips already-fully-enriched records in Python, stopping
    once `limit` still-needing records are collected.

    Without this, every run queried with offset 0 and no state tracked
    between runs, so it wasn't paginating through the target population at
    all -- it re-sampled whatever "first N" a live, constantly-changing
    workspace happened to return each time, with no guarantee of ever
    reaching everyone and no way to avoid re-pulling already-done records
    (see DESIGN.md §1, 2026-09-17 addendum, for the full diagnosis).

    `max_scan` is a safety cap on how many records this will look at
    before giving up short of `limit` records collected (only matters if
    the personae_type population is mostly already fully enriched); not
    expected to bind in normal operation.
    """
    collected = []
    offset = 0
    scanned = 0
    while len(collected) < limit and scanned < max_scan:
        page = _fetch_people_page(page_size, offset)
        if not page:
            break
        for raw in page:
            scanned += 1
            if _still_needs_enrichment(raw):
                collected.append(raw)
                if len(collected) >= limit:
                    break
        offset += page_size
    return collected


def get_record_values(record):
    """Attio record values come back as {attr: [{value: ...}, ...]}; flatten
    the handful this job cares about."""
    values = record.get("values", {})

    def first(attr):
        v = values.get(attr) or []
        return v[0] if v else None

    linkedin = first("linkedin")
    emails = [e.get("email_address") for e in (values.get("email_addresses") or [])]
    return {
        "id": record["id"]["record_id"],
        "name": (first("name") or {}).get("full_name"),
        "linkedin_url": (linkedin or {}).get("value"),
        "job_title": (first("job_title") or {}).get("value"),
        "primary_location": first("primary_location"),
        "company_record_id": (
            (first("company") or {}).get("target_record_id")
        ),
        "email_addresses": [e for e in emails if e],
    }


def get_person_by_id(record_id: str):
    """Fetch a single person record by id -- used by the on-demand Lemlist
    handoff (never reuse query_target_people() for a single-record lookup)."""
    resp = requests.get(
        f"{ATTIO_BASE_URL}/objects/{ATTIO_PEOPLE_OBJECT}/records/{record_id}",
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return get_record_values(resp.json()["data"])


def get_company_info(record_id: str):
    """Fetch a company's name and LinkedIn company-page URL (when known).
    Used by Branch B (see DESIGN.md §4) to corroborate a LinkedIn match
    against the person's actual employer -- first as a cheap free-text
    check against the candidate's headline, then, if that's inconclusive,
    to resolve the company's own LinkedIn id for a structured
    disambiguation search on Jules Ferrer's Sales Navigator account."""
    resp = requests.get(
        f"{ATTIO_BASE_URL}/objects/companies/records/{record_id}",
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    values = resp.json()["data"].get("values", {})

    def first(attr):
        v = values.get(attr) or []
        return v[0] if v else None

    return {
        "name": (first("name") or {}).get("value"),
        "linkedin_url": (first("linkedin") or {}).get("value"),
    }


def update_person(record_id, attrs: dict):
    """PATCH a subset of attribute values onto a person record."""
    payload = {"data": {"values": {k: [v] if not isinstance(v, list) else v for k, v in attrs.items()}}}
    resp = requests.patch(
        f"{ATTIO_BASE_URL}/objects/{ATTIO_PEOPLE_OBJECT}/records/{record_id}",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def flag_needs_linkedin_review(record_id, reason: str, candidates: list):
    """
    Branch B, ambiguous/weak match: never guess. Leave a Note on the record
    instead of writing anything, so a human confirms the match manually.
    (See DESIGN.md §4 for why this uses a Note rather than a new attribute.)
    """
    lines = [f"**Needs LinkedIn Review** — {reason}", ""]
    for c in candidates:
        lines.append(f"- {c.get('name', '?')} — {c.get('public_profile_url', '?')} "
                      f"(headline: {c.get('headline', '?')})")
    body = "\n".join(lines)
    resp = requests.post(
        f"{ATTIO_BASE_URL}/notes",
        headers=_headers(),
        json={
            "data": {
                "parent_object": ATTIO_PEOPLE_OBJECT,
                "parent_record_id": record_id,
                "title": "Needs LinkedIn Review",
                "format": "plaintext",
                "content": body,
            }
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()
