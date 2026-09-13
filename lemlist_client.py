"""
Lemlist is not provisioned yet (confirmed with user: no account/API key/
campaign exists — DESIGN.md §8). This stub keeps the on-demand handoff
runnable end-to-end (email fetch + Attio write) today, and fails loudly
and specifically if anyone tries to actually push to Lemlist before it's
configured — rather than silently no-op'ing.
"""
import config


class LemlistNotConfigured(RuntimeError):
    pass


def push_contact(email: str, first_name: str | None, last_name: str | None):
    if not config.LEMLIST_API_KEY or not config.LEMLIST_CAMPAIGN_ID:
        raise LemlistNotConfigured(
            "LEMLIST_API_KEY / LEMLIST_CAMPAIGN_ID are not set. Provision a "
            "Lemlist account, API key, and target campaign before enabling "
            "this. See DESIGN.md §8/§10."
        )
    import requests

    resp = requests.post(
        f"https://api.lemlist.com/api/campaigns/{config.LEMLIST_CAMPAIGN_ID}/leads/{email}",
        auth=("", config.LEMLIST_API_KEY),
        json={"firstName": first_name, "lastName": last_name},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()
