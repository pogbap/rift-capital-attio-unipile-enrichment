"""
Offline smoke test -- no real Attio/Unipile calls. Monkeypatches the network
boundary functions to exercise Branch A, Branch B (confident match), Branch B
(ambiguous -> flagged), and the fill-if-empty guard, then asserts on the
resulting run_stats. Run with: python test_smoke.py
"""
import types

import attio_client
import enrichment
import unipile_client

written = {"updates": [], "notes": []}


def fake_query_target_people(limit=50, offset=0):
    return [
        {"id": {"record_id": "p1"}, "values": {
            "name": [{"full_name": "Alice Dupont"}],
            "linkedin": [{"value": "https://linkedin.com/in/alice-dupont-1234"}],
            "job_title": [{"value": "Partner"}],
        }},  # Branch A, empty location -> should write
        {"id": {"record_id": "p2"}, "values": {
            "name": [{"full_name": "Bob Martin"}],
            "job_title": [{"value": "CIO"}],
        }},  # Branch B, single strong match -> should write linkedin+location
        {"id": {"record_id": "p3"}, "values": {
            "name": [{"full_name": "Common Name"}],
            "job_title": [{"value": "Advisor"}],
        }},  # Branch B, 2 candidates -> should flag, no writes
        {"id": {"record_id": "p4"}, "values": {
            "name": [{"full_name": "Chloe Petit"}],
            "linkedin": [{"value": "https://linkedin.com/in/chloe-petit"}],
            "job_title": [{"value": "GP"}],
            "primary_location": [{"locality": "Paris", "country_code": "FR"}],
        }},  # Branch A, location already set -> must NOT overwrite
    ]


def fake_update_person(record_id, attrs):
    written["updates"].append((record_id, attrs))
    return {}


def fake_flag(record_id, reason, candidates):
    written["notes"].append((record_id, reason))
    return {}


def fake_get_profile(handle_or_id, account_id=None):
    return {"location": "London", "country": "United Kingdom"}


def fake_search_people(name, company=None, title=None, account_id=None):
    if name == "Bob Martin":
        return [{"name": "Bob Martin", "headline": "CIO at Acme", "public_profile_url": "https://linkedin.com/in/bobmartin"}]
    if name == "Common Name":
        return [
            {"name": "Common Name", "headline": "Advisor at X", "public_profile_url": "https://linkedin.com/in/cn1"},
            {"name": "Common Name", "headline": "Advisor at Y", "public_profile_url": "https://linkedin.com/in/cn2"},
        ]
    return []


attio_client.query_target_people = fake_query_target_people
attio_client.update_person = fake_update_person
attio_client.flag_needs_linkedin_review = fake_flag
unipile_client.get_profile = fake_get_profile
unipile_client.search_people = fake_search_people
unipile_client._sleep_before_call = lambda: 0  # no real sleeping in the smoke test

stats = enrichment.run_batch()
print("run_stats:", stats)
print("attio writes:", written["updates"])
print("flagged notes:", written["notes"])

assert stats["records_processed"] == 4
assert any(rid == "p1" for rid, _ in written["updates"]), "Branch A should have written p1's location"
assert any(rid == "p2" and "linkedin" in attrs for rid, attrs in written["updates"]), "Branch B strong match should write linkedin for p2"
assert any(rid == "p3" for rid, _ in written["notes"]), "Ambiguous Branch B (p3) should be flagged for review"
assert not any(rid == "p3" and "linkedin" in attrs for rid, attrs in written["updates"]), "Ambiguous Branch B (p3) must NOT get a LinkedIn write, confident match or not"
assert any(rid == "p3" and "primary_location" in attrs for rid, attrs in written["updates"]), (
    "Ambiguous Branch B (p3) should still get a best-effort location write from the top candidate "
    "(2026-09-14 policy: locations matter more than 100%-clean LinkedIn matches here)"
)
assert not any(rid == "p4" for rid, _ in written["updates"]), "p4 already has a location -- fill-if-empty must skip it"

print("\nSMOKE TEST PASSED")
