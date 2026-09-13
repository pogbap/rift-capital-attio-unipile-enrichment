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


def query_target_people(limit=50, offset=0):
    """
    People whose personae_type (multiselect) contains at least one of the
    target option titles. See DESIGN.md §1 for why $in replaces the source
    scenario's 7 OR'd $eq conditions.
    """
    body = {
        "filter": {"personae_type": {"$in": TARGET_PERSONAE_TYPES}},
        "limit": limit,
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
