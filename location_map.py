"""
Map Unipile's free-text city/country into Attio's structured `location`
attribute shape (DESIGN.md §2). Attio requires every key present (null where
unknown) — a partial object is rejected.

Country -> ISO 3166-1 alpha-2 is a best-effort lookup covering common cases
seen in Rift Capital's investor base; extend COUNTRY_TO_ALPHA2 as needed
rather than guessing wrong (a wrong country_code is worse than a blank one).
"""

COUNTRY_TO_ALPHA2 = {
    "france": "FR", "united states": "US", "united states of america": "US",
    "usa": "US", "united kingdom": "GB", "uk": "GB", "switzerland": "CH",
    "suisse": "CH", "luxembourg": "LU", "belgium": "BE", "belgique": "BE",
    "monaco": "MC", "united arab emirates": "AE", "uae": "AE",
    "singapore": "SG", "hong kong": "HK", "germany": "DE", "spain": "ES",
    "italy": "IT", "netherlands": "NL", "canada": "CA",
}


def to_attio_location(city: str | None, country: str | None) -> dict:
    country_code = None
    if country:
        country_code = COUNTRY_TO_ALPHA2.get(country.strip().lower())
    return {
        "line_1": None,
        "line_2": None,
        "line_3": None,
        "line_4": None,
        "locality": city or None,
        "region": None,
        "postcode": None,
        "country_code": country_code,
        "latitude": None,
        "longitude": None,
    }
