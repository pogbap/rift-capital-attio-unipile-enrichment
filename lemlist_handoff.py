"""
On-demand, per-contact action (DESIGN.md §8). NOT part of the scheduled
batch job -- run this by hand for one Attio person at a time.

    python lemlist_handoff.py <attio_record_id> [--confirm-push]

Behavior:
  1. If the person already has an email in Attio's email_addresses, use it.
     Otherwise, attempt to fetch one via Unipile from their LinkedIn profile
     (8-13s pacing sleep applied, same as every other Unipile call).
  2. If a new email was found, write it to Attio's email_addresses -- only
     if one wasn't already present (never overwrites an existing address).
  3. Without --confirm-push (the default): print the email found and the
     exact payload that *would* be sent to Lemlist, then stop. Nothing is
     pushed to Lemlist.
  4. With --confirm-push: actually call Lemlist. Raises LemlistNotConfigured
     until real Lemlist credentials are set (see lemlist_client.py) -- by
     design, since Lemlist isn't provisioned yet.

This two-step gate exists because the user scoped this step to "find the
email when we don't have it" -- not to auto-enroll anyone in outreach. The
push to Lemlist must always be a deliberate, confirmed, human action, never
something the scheduled enrichment job triggers on its own.
"""
import argparse
import re
import sys

import attio_client
import lemlist_client
import unipile_client

LINKEDIN_HANDLE_RE = re.compile(r"/in/([^/?#]+)")


def extract_handle(linkedin_url: str):
    m = LINKEDIN_HANDLE_RE.search(linkedin_url or "")
    return m.group(1) if m else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record_id", help="Attio person record_id")
    parser.add_argument(
        "--confirm-push", action="store_true",
        help="Actually push to Lemlist (otherwise: dry-run, prints intended payload and stops)",
    )
    args = parser.parse_args()

    person = attio_client.get_person_by_id(args.record_id)
    name = person["name"] or "(unnamed)"

    if person["email_addresses"]:
        email = person["email_addresses"][0]
        print(f"{name}: already has an email on file in Attio ({email}); not calling Unipile.")
    else:
        handle = extract_handle(person["linkedin_url"])
        if not handle:
            print(f"{name}: no email on file and no usable LinkedIn URL -- nothing to do.")
            sys.exit(1)
        email = unipile_client.get_email(handle)
        if not email:
            print(f"{name}: Unipile could not resolve an email for this profile.")
            sys.exit(1)
        attio_client.update_person(person["id"], {"email_addresses": [{"email_address": email}]})
        print(f"{name}: found {email} via Unipile and wrote it to Attio email_addresses.")

    first_name, last_name = (name.split(" ", 1) + [""])[:2]
    payload_preview = {"campaign_id": "<LEMLIST_CAMPAIGN_ID>", "email": email,
                        "firstName": first_name, "lastName": last_name}

    if not args.confirm_push:
        print("\nDry run only -- nothing was sent to Lemlist. Intended payload:")
        print(payload_preview)
        print("\nRe-run with --confirm-push once you've reviewed this and Lemlist is provisioned.")
        return

    result = lemlist_client.push_contact(email, first_name, last_name)
    print(f"\nPushed to Lemlist: {result}")


if __name__ == "__main__":
    main()
