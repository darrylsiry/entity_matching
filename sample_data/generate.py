"""Generate two demo company files with known overlaps and deliberate variations.

Produces file_a.csv and file_b.csv in this directory. Records that should match carry
the same underlying company but differ in name format, casing, suffix, domain style,
address wording, or ID punctuation — exercising every stage of the cascade.

Run:  python sample_data/generate.py
"""

from __future__ import annotations

import csv
from pathlib import Path

HERE = Path(__file__).resolve().parent

# (company, file-A row, file-B row). B is None -> A-only (no match expected).
COMPANIES = [
    # exact reg-id match despite very different names -> deterministic (id)
    (dict(name="Acme, Inc.", website="acme.com", ein="12-3456789",
          address="1 Market St", city="San Francisco", postcode="94105", country="USA"),
     dict(name="ACME INCORPORATED", website="", ein="123456789",
          address="One Market Street", city="San Francisco", postcode="94105", country="US")),
    # same domain, different name -> deterministic (domain)
    (dict(name="Globex Corporation", website="https://www.globex.com", ein="",
          address="500 Blvd", city="Austin", postcode="73301", country="USA"),
     dict(name="Globex", website="globex.com/contact", ein="",
          address="500 Boulevard", city="Austin", postcode="73301", country="US")),
    # fuzzy name + same city/postcode -> probabilistic/fuzzy
    (dict(name="Initech LLC", website="", ein="",
          address="200 Tech Way", city="Boston", postcode="02110", country="USA"),
     dict(name="Initech Technologies", website="", ein="",
          address="200 Technology Way", city="Boston", postcode="02110", country="US")),
    # near-identical name, minor typo -> fuzzy
    (dict(name="Umbrella Health Group", website="", ein="",
          address="9 Raccoon Rd", city="Raccoon City", postcode="44101", country="USA"),
     dict(name="Umbrela Health Group", website="", ein="",
          address="9 Raccoon Road", city="Raccoon City", postcode="44101", country="US")),
    # unicode / suffix variation, matching domain -> deterministic (domain)
    (dict(name="Café Wayne Ltd.", website="wayne-enterprises.co.uk", ein="",
          address="1007 Mountain Dr", city="Gotham", postcode="10001", country="UK"),
     dict(name="Cafe Wayne", website="https://wayne-enterprises.co.uk/", ein="",
          address="1007 Mountain Drive", city="Gotham", postcode="10001", country="GB")),
    # VAT id with spaces vs compact -> deterministic (id)
    (dict(name="Stark Industries GmbH", website="", ein="GB 123 4567 89",
          address="10 Ave", city="Berlin", postcode="10115", country="DE"),
     dict(name="Stark Industries", website="", ein="GB123456789",
          address="10 Avenue", city="Berlin", postcode="10115", country="DE")),
    # A-only, no match in B
    (dict(name="Soylent Foods Inc", website="soylent.example", ein="55-5555555",
          address="7 Green St", city="Portland", postcode="97035", country="USA"),
     None),
    # ambiguous-ish: similar name but should still match its true pair
    (dict(name="Hooli Inc.", website="hooli.com", ein="",
          address="1 Hooli Way", city="Palo Alto", postcode="94301", country="USA"),
     dict(name="Hooli", website="www.hooli.com", ein="",
          address="1 Hooli Way", city="Palo Alto", postcode="94301", country="US")),
]

# B-only distractors that resemble nothing in A.
B_ONLY = [
    dict(name="Wonka Industries", website="wonka.example", ein="",
         address="1 Chocolate Ln", city="London", postcode="EC1A 1BB", country="UK"),
    dict(name="Cyberdyne Systems", website="cyberdyne.example", ein="",
         address="18144 El Camino", city="Sunnyvale", postcode="94086", country="USA"),
]

FIELDS = ["name", "website", "ein", "address", "city", "postcode", "country"]


def _write(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in FIELDS})


# Single-file dataset for dedupe mode: several companies appear on more than one row
# with variations, plus some rows that are unique.
DEDUPE_ROWS = [
    dict(name="Acme, Inc.", website="acme.com", ein="12-3456789",
         address="1 Market St", city="San Francisco", postcode="94105", country="USA"),
    dict(name="ACME INCORPORATED", website="", ein="123456789",
         address="One Market Street", city="San Francisco", postcode="94105", country="US"),
    dict(name="Acme", website="https://www.acme.com/about", ein="",
         address="1 Market", city="SF", postcode="94105", country="USA"),
    dict(name="Globex Corporation", website="globex.com", ein="",
         address="500 Blvd", city="Austin", postcode="73301", country="USA"),
    dict(name="Globex Corp", website="www.globex.com", ein="",
         address="500 Boulevard", city="Austin", postcode="73301", country="US"),
    dict(name="Initech LLC", website="", ein="",
         address="200 Tech Way", city="Boston", postcode="02110", country="USA"),
    dict(name="Initech Technologies", website="", ein="",
         address="200 Technology Way", city="Boston", postcode="02110", country="US"),
    dict(name="Umbrella Health Group", website="", ein="",
         address="9 Raccoon Rd", city="Raccoon City", postcode="44101", country="USA"),
    dict(name="Umbrela Health Group", website="", ein="",
         address="9 Raccoon Road", city="Raccoon City", postcode="44101", country="US"),
    dict(name="Wonka Industries", website="wonka.example", ein="",
         address="1 Chocolate Ln", city="London", postcode="EC1A 1BB", country="UK"),
    dict(name="Cyberdyne Systems", website="cyberdyne.example", ein="",
         address="18144 El Camino", city="Sunnyvale", postcode="94086", country="USA"),
    dict(name="Stark Industries", website="", ein="",
         address="10 Ave", city="New York", postcode="10001", country="USA"),
]


def main() -> None:
    a_rows = [a for a, _ in COMPANIES]
    b_rows = [b for _, b in COMPANIES if b is not None] + B_ONLY
    _write(HERE / "file_a.csv", a_rows)
    _write(HERE / "file_b.csv", b_rows)
    _write(HERE / "file_dedupe.csv", DEDUPE_ROWS)
    expected = sum(1 for _, b in COMPANIES if b is not None)
    # Dedupe expectation: Acme x3, Globex x2, Initech x2, Umbrella x2 -> 4 dup groups.
    print(f"Wrote file_a.csv ({len(a_rows)} rows), file_b.csv ({len(b_rows)} rows)")
    print(f"Expected two-file matches: {expected}")
    print(f"Wrote file_dedupe.csv ({len(DEDUPE_ROWS)} rows) — expect 7 entities "
          f"(Acme×3, Globex×2, Initech×2, Umbrella×2, + 3 singletons)")


if __name__ == "__main__":
    main()
