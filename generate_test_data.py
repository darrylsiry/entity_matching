"""Generate two dummy company CSV files for entity-matching testing.

Output:
  companies_file_a.csv  - 1000 companies
  companies_file_b.csv  - 1000 companies
  ground_truth_matches.csv - the 200 true overlapping pairs (for scoring)

Rules:
  - Every row has a company name and address
  - ~10% of rows have a DUNS number, ~20% have a website URL
  - 200 companies (20%) appear in both files, with realistic variations
    (name suffix changes, abbreviations, typos, case differences)
"""

import csv
import random
import re

random.seed(42)

# ---------- name generation ----------
WORDS1 = ["Apex", "Summit", "Pinnacle", "Vertex", "Horizon", "Cascade", "Sterling",
          "Granite", "Ironwood", "Bluewater", "Redstone", "Silverline", "Northgate",
          "Eastbrook", "Westfield", "Lakeshore", "Riverbend", "Clearview", "Brightpath",
          "Goldleaf", "Copperfield", "Stonebridge", "Oakmont", "Maplewood", "Cedarcrest",
          "Pineview", "Elmwood", "Birchwood", "Willowbrook", "Ashford", "Harborview",
          "Baycrest", "Seaside", "Coastal", "Inland", "Midland", "Central", "Pacific",
          "Atlantic", "Continental", "National", "Global", "United", "Allied", "Premier",
          "Prime", "Elite", "Superior", "Advanced", "Dynamic", "Innovative", "Precision",
          "Reliable", "Trusted", "Heritage", "Legacy", "Pioneer", "Frontier", "Vanguard",
          "Beacon", "Compass", "Anchor", "Keystone", "Cornerstone", "Foundation",
          "Meridian", "Zenith", "Nova", "Stellar", "Orion", "Polaris", "Aurora",
          "Solstice", "Equinox", "Catalyst", "Momentum", "Velocity", "Quantum",
          "Fusion", "Synergy", "Nexus", "Matrix", "Vector", "Axiom", "Paradigm",
          "Spectrum", "Prism", "Crystal", "Diamond", "Emerald", "Sapphire", "Onyx",
          "Cobalt", "Titanium", "Platinum", "Falcon", "Eagle", "Hawk", "Raven",
          "Phoenix", "Griffin", "Pegasus", "Atlas", "Titan", "Hercules", "Mercury",
          "Neptune", "Jupiter", "Saturn", "Delta", "Omega", "Sigma", "Alpha", "Everest",
          "Denali", "Rainier", "Shasta", "Sierra", "Rockwell", "Bradford", "Kensington",
          "Wellington", "Hamilton", "Franklin", "Jefferson", "Madison", "Monroe",
          "Harrison", "Lancaster", "Windsor", "Devonshire", "Ashworth", "Blackstone"]

WORDS2 = ["Manufacturing", "Industries", "Logistics", "Technologies", "Solutions",
          "Systems", "Services", "Consulting", "Engineering", "Construction",
          "Builders", "Contractors", "Electric", "Plumbing", "Mechanical", "HVAC",
          "Fabrication", "Machining", "Tooling", "Plastics", "Metals", "Steel",
          "Lumber", "Hardware", "Supply", "Distribution", "Wholesale", "Trading",
          "Imports", "Freight", "Transport", "Trucking", "Shipping", "Warehousing",
          "Packaging", "Printing", "Publishing", "Media", "Marketing", "Advertising",
          "Communications", "Networks", "Software", "Analytics", "Digital", "Data",
          "Capital", "Financial", "Insurance", "Realty", "Properties", "Development",
          "Holdings", "Ventures", "Partners", "Associates", "Advisors", "Management",
          "Staffing", "Recruiting", "Healthcare", "Medical", "Dental", "Pharmacy",
          "Laboratories", "Diagnostics", "Foods", "Beverage", "Farms", "Produce",
          "Dairy", "Bakery", "Restaurants", "Hospitality", "Cleaning", "Landscaping",
          "Roofing", "Paving", "Excavating", "Drilling", "Energy", "Power", "Solar",
          "Environmental", "Recycling", "Automotive", "Aerospace", "Marine", "Rail",
          "Textiles", "Apparel", "Furniture", "Flooring", "Glass", "Concrete",
          "Chemical", "Coatings", "Adhesives", "Instruments", "Controls", "Robotics",
          "Security", "Safety", "Fire Protection", "Equipment", "Rentals", "Leasing"]

SUFFIXES = ["Inc", "Inc.", "LLC", "L.L.C.", "Corp", "Corp.", "Corporation", "Co",
            "Co.", "Company", "Ltd", "Ltd.", "Group", "Enterprises", "LP", "LLP", ""]

STREET_NAMES = ["Main", "Oak", "Maple", "Cedar", "Pine", "Elm", "Washington",
                "Lincoln", "Jefferson", "Madison", "Franklin", "Park", "Lake",
                "Hill", "River", "Spring", "Church", "Market", "Broad", "Center",
                "Union", "Liberty", "Commerce", "Industrial", "Enterprise",
                "Corporate", "Technology", "Innovation", "Progress", "Prospect",
                "Highland", "Sunset", "Ridge", "Valley", "Meadow", "Forest",
                "Grove", "Garden", "Harbor", "Bay", "Beach", "College", "State",
                "Mill", "Bridge", "Depot", "Station", "Airport", "Fairview", "Walnut"]

STREET_TYPES = [("Street", "St"), ("Avenue", "Ave"), ("Boulevard", "Blvd"),
                ("Drive", "Dr"), ("Road", "Rd"), ("Lane", "Ln"), ("Way", "Way"),
                ("Court", "Ct"), ("Parkway", "Pkwy"), ("Place", "Pl")]

CITIES = [("Springfield", "IL", "62701"), ("Riverside", "CA", "92501"),
          ("Franklin", "TN", "37064"), ("Georgetown", "TX", "78626"),
          ("Clinton", "NJ", "08809"), ("Salem", "OR", "97301"),
          ("Fairview", "PA", "16415"), ("Madison", "WI", "53703"),
          ("Arlington", "VA", "22201"), ("Bristol", "CT", "06010"),
          ("Dover", "DE", "19901"), ("Hudson", "OH", "44236"),
          ("Kingston", "NY", "12401"), ("Lancaster", "PA", "17601"),
          ("Manchester", "NH", "03101"), ("Newport", "RI", "02840"),
          ("Oxford", "MS", "38655"), ("Plymouth", "MN", "55441"),
          ("Quincy", "MA", "02169"), ("Richmond", "VA", "23219"),
          ("Aurora", "CO", "80010"), ("Boulder", "CO", "80301"),
          ("Charlotte", "NC", "28202"), ("Denver", "CO", "80202"),
          ("Evanston", "IL", "60201"), ("Fresno", "CA", "93701"),
          ("Greenville", "SC", "29601"), ("Houston", "TX", "77002"),
          ("Irvine", "CA", "92602"), ("Jacksonville", "FL", "32202"),
          ("Knoxville", "TN", "37902"), ("Louisville", "KY", "40202"),
          ("Memphis", "TN", "38103"), ("Nashville", "TN", "37201"),
          ("Omaha", "NE", "68102"), ("Portland", "OR", "97201"),
          ("Raleigh", "NC", "27601"), ("Sacramento", "CA", "95814"),
          ("Tampa", "FL", "33602"), ("Tucson", "AZ", "85701"),
          ("Wichita", "KS", "67202"), ("Albany", "NY", "12207"),
          ("Boise", "ID", "83702"), ("Chattanooga", "TN", "37402"),
          ("Des Moines", "IA", "50309"), ("El Paso", "TX", "79901"),
          ("Fort Wayne", "IN", "46802"), ("Grand Rapids", "MI", "49503"),
          ("Harrisburg", "PA", "17101"), ("Indianapolis", "IN", "46204")]

STATE_NAMES = {"IL": "Illinois", "CA": "California", "TN": "Tennessee",
               "TX": "Texas", "NJ": "New Jersey", "OR": "Oregon",
               "PA": "Pennsylvania", "WI": "Wisconsin", "VA": "Virginia",
               "CT": "Connecticut", "DE": "Delaware", "OH": "Ohio",
               "NY": "New York", "NH": "New Hampshire", "RI": "Rhode Island",
               "MS": "Mississippi", "MN": "Minnesota", "MA": "Massachusetts",
               "CO": "Colorado", "NC": "North Carolina", "SC": "South Carolina",
               "FL": "Florida", "KY": "Kentucky", "NE": "Nebraska",
               "AZ": "Arizona", "KS": "Kansas", "IA": "Iowa", "ID": "Idaho",
               "IN": "Indiana", "MI": "Michigan"}


def make_companies(n, used_names):
    companies = []
    while len(companies) < n:
        base = f"{random.choice(WORDS1)} {random.choice(WORDS2)}"
        if base in used_names:
            continue
        used_names.add(base)
        suffix = random.choice(SUFFIXES)
        name = f"{base} {suffix}".strip()
        num = random.randint(1, 9999)
        street = random.choice(STREET_NAMES)
        stype_full, stype_abbr = random.choice(STREET_TYPES)
        city, state, zipc = random.choice(CITIES)
        suite = f" Suite {random.randint(100, 999)}" if random.random() < 0.25 else ""
        address = f"{num} {street} {stype_full}{suite}, {city}, {state} {zipc}"
        duns = f"{random.randint(10000000, 99999999)}{random.randint(0, 9)}" if random.random() < 0.10 else ""
        domain = re.sub(r"[^a-z0-9]", "", base.lower())[:20]
        website = f"https://www.{domain}.com" if random.random() < 0.20 else ""
        companies.append({
            "base": base, "name": name, "num": num, "street": street,
            "stype_full": stype_full, "stype_abbr": stype_abbr, "suite": suite,
            "city": city, "state": state, "zip": zipc,
            "address": address, "duns": duns, "website": website,
        })
    return companies


def introduce_typo(s):
    """Swap two adjacent letters somewhere in the middle of a word."""
    idx = [i for i in range(1, len(s) - 1) if s[i].isalpha() and s[i + 1].isalpha()]
    if not idx:
        return s
    i = random.choice(idx)
    return s[:i] + s[i + 1] + s[i] + s[i + 2:]


SUFFIX_SWAPS = {"Inc": "Incorporated", "Inc.": "Inc", "LLC": "L.L.C.",
                "L.L.C.": "LLC", "Corp": "Corporation", "Corp.": "Corp",
                "Corporation": "Corp.", "Co": "Company", "Co.": "Co",
                "Company": "Co.", "Ltd": "Limited", "Ltd.": "Ltd"}


def vary_name(c):
    name = c["name"]
    r = random.random()
    if r < 0.30:  # swap legal suffix form
        for old, new in SUFFIX_SWAPS.items():
            if name.endswith(" " + old):
                return name[: -len(old)] + new
        return name + " Inc"
    elif r < 0.45:  # drop suffix entirely
        for sfx in sorted(SUFFIX_SWAPS, key=len, reverse=True):
            if name.endswith(" " + sfx):
                return name[: -(len(sfx) + 1)]
        return name
    elif r < 0.60:  # all caps
        return name.upper()
    elif r < 0.72:  # typo in name
        return introduce_typo(name)
    elif r < 0.82:  # "and" <-> "&" or add "The"
        return "The " + name
    elif r < 0.92:  # comma before suffix
        for sfx in sorted(SUFFIX_SWAPS, key=len, reverse=True):
            if name.endswith(" " + sfx):
                return name[: -(len(sfx) + 1)] + ", " + sfx
        return name
    else:  # unchanged (exact name, other fields may differ)
        return name


def vary_address(c):
    r = random.random()
    suite = c["suite"]
    stype = c["stype_full"]
    city, state, zipc = c["city"], c["state"], c["zip"]
    if r < 0.35:  # abbreviate street type
        stype = c["stype_abbr"]
    elif r < 0.50:  # abbreviate suite
        suite = suite.replace(" Suite ", " Ste ")
    elif r < 0.62:  # spell out state
        state = STATE_NAMES.get(state, state)
    elif r < 0.72:  # drop suite
        suite = ""
    elif r < 0.82:  # zip+4
        zipc = f"{zipc}-{random.randint(1000, 9999)}"
    elif r < 0.92:  # typo in street name
        return f"{c['num']} {introduce_typo(c['street'])} {stype}{suite}, {city}, {state} {zipc}"
    return f"{c['num']} {c['street']} {stype}{suite}, {city}, {state} {zipc}"


def vary_website(c, base):
    domain = re.sub(r"[^a-z0-9]", "", base.lower())[:20]
    return random.choice([f"https://www.{domain}.com", f"http://{domain}.com",
                          f"www.{domain}.com", f"https://{domain}.com"])


def main(outdir="."):
    used = set()
    overlap = make_companies(200, used)      # in both files
    only_a = make_companies(800, used)
    only_b = make_companies(800, used)

    # File A: overlap companies as-is + only_a
    rows_a = [{"company_name": c["name"], "company_address": c["address"],
               "duns_number": c["duns"], "website_url": c["website"]}
              for c in overlap + only_a]

    # File B: varied versions of overlap + only_b
    rows_b = []
    truth = []
    for c in overlap:
        name_b = vary_name(c)
        addr_b = vary_address(c)
        # DUNS: keep it stable when present; independently hit ~10% presence
        duns_b = c["duns"] if (c["duns"] and random.random() < 0.6) else (
            f"{random.randint(100000000, 999999999)}" if random.random() < 0.055 else "")
        web_b = vary_website(c, c["base"]) if (c["website"] and random.random() < 0.7) else (
            vary_website(c, c["base"]) if random.random() < 0.07 else "")
        rows_b.append({"company_name": name_b, "company_address": addr_b,
                       "duns_number": duns_b, "website_url": web_b})
        truth.append({"file_a_name": c["name"], "file_b_name": name_b,
                      "file_a_address": c["address"], "file_b_address": addr_b})
    rows_b += [{"company_name": c["name"], "company_address": c["address"],
                "duns_number": c["duns"], "website_url": c["website"]}
               for c in only_b]

    random.shuffle(rows_a)
    random.shuffle(rows_b)

    for fname, rows in [("companies_file_a.csv", rows_a),
                        ("companies_file_b.csv", rows_b)]:
        with open(f"{outdir}/{fname}", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["company_name", "company_address",
                                              "duns_number", "website_url"])
            w.writeheader()
            w.writerows(rows)

    with open(f"{outdir}/ground_truth_matches.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["file_a_name", "file_b_name",
                                          "file_a_address", "file_b_address"])
        w.writeheader()
        w.writerows(truth)

    # quick stats
    for label, rows in [("A", rows_a), ("B", rows_b)]:
        d = sum(1 for r in rows if r["duns_number"])
        wb = sum(1 for r in rows if r["website_url"])
        print(f"File {label}: {len(rows)} rows, {d} DUNS ({d/len(rows):.0%}), "
              f"{wb} websites ({wb/len(rows):.0%})")
    print(f"Overlap: {len(truth)} companies (ground_truth_matches.csv)")


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
