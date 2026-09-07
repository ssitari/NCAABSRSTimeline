#!/usr/bin/env python3
"""
Cross-reference SRS schools with real, sourced team colors.

Output (written next to this script):
  team_colors.csv - school (matches srs_ratings.csv's `school` column),
                     primary_hex, secondary_hex, matched_name (the source
                     dataset's name, for spot-checking the match)

Source
------
glidej/ncaa-team-colors (GitHub, MIT-style small open dataset), 346 current
NCAA programs with real hex colors:
    https://github.com/glidej/ncaa-team-colors
    https://raw.githubusercontent.com/glidej/ncaa-team-colors/master/ncaa-team-colors.json

This only covers ~346 current programs, so most historical/defunct schools
in srs_ratings.csv (1950-2026, ~350+ Division I programs) won't get a match here.
That's expected and fine -- unmatched schools fall back to a color derived
from their conference in the front end, rather than a guessed color. Nothing
in this file invents a hex code; every value traces to the source JSON.

Matching: the source names are "{School Name} {Mascot}" (e.g. "Ohio State
Buckeyes", "Miami (FL) Hurricanes"). For each source entry, find every SRS
school name that is a whitespace-boundary prefix of it (e.g. both
"Mississippi" and, for a different entry, "Mississippi State" can be valid
prefixes of different entries) and keep the LONGEST matching SRS name, so
"Mississippi State Bulldogs" resolves to "Mississippi State" and not the
shorter "Mississippi".

Stdlib only. Downloads cache into data/; pass --refresh to re-download.
"""

import argparse
import csv
import json
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
SRS_CSV = HERE / "srs_ratings.csv"
OUTPUT_CSV = HERE / "team_colors.csv"

SOURCE_URL = (
    "https://raw.githubusercontent.com/glidej/ncaa-team-colors/master/"
    "ncaa-team-colors.json"
)
CACHE_FILE = DATA_DIR / "ncaa-team-colors.json"
USER_AGENT = "NCAABSRSTimeline research script (contact: ecg2104@columbia.edu)"

# The prefix matcher below can't bridge cases where the color dataset and
# Sports Reference use unrelated names for the same program (an abbreviation
# on one side, a parenthetical qualifier or an ambiguous shared prefix on the
# other). Map the color entry's exact `name` to the SRS `school` by hand for
# those. The colors themselves still come straight from the source JSON --
# this only fixes *which* school they attach to.
NAME_OVERRIDES = {
    "Virginia Cavaliers": "Virginia",
    "Virginia Military Institute Keydets": "VMI",
    "North Carolina State Wolfpack": "NC State",
    "UNLV Rebels": "Nevada-Las Vegas",
    "LSU Tigers": "Louisiana State",
    "USC Trojans": "Southern California",
    "St. John's Red Storm": "St. John's (NY)",
    "Saint Mary's Gaels": "Saint Mary's",
    "TCU Horned Frogs": "Texas Christian",
    "Detroit Titans": "Detroit Mercy",
    "Long Island Blackbirds": "Long Island University",
    "Louisiana-Lafayette Ragin' Cajuns": "Louisiana",
    "Southern Miss Golden Eagles": "Southern Mississippi",
    "Southern University Jaguars": "Southern",
}


def fetch_source(refresh: bool) -> list[dict]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if CACHE_FILE.exists() and not refresh:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    CACHE_FILE.write_bytes(raw)
    return json.loads(raw.decode("utf-8"))


def normalize_words(name: str) -> list[str]:
    name = name.replace("&", "and")
    name = re.sub(r"[^\w\s()-]", "", name)
    return name.lower().split()


def load_srs_schools() -> set[str]:
    schools = set()
    with SRS_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            schools.add(row["school"])
    return schools


def best_match(source_words: list[str], srs_school_words: dict[str, list[str]]) -> str | None:
    best = None
    best_len = 0
    for school, words in srs_school_words.items():
        n = len(words)
        if n > best_len and n <= len(source_words) and source_words[:n] == words:
            best = school
            best_len = n
    return best


# Words that, immediately after a matched SRS-school prefix, signal the
# source entry actually names a DIFFERENT sister institution not present in
# the SRS data (e.g. "Alabama State", "Texas A&M-CC", "USC Upstate") rather
# than a mascot (e.g. "Alabama Crimson Tide"). Used only to break ties when
# two source entries collide on the same short SRS prefix.
INSTITUTION_CONTINUATION_WORDS = {
    "state", "aandm", "aandm-cc", "aandt", "tech", "international", "central",
    "commonwealth", "baptist", "christian", "upstate", "gulf", "pine-bluff",
    "panhandle", "valley", "northern", "southern", "western", "eastern",
    "university", "college", "methodist", "miss", "utah",
}


def resolve_collisions(
    entry_school_pairs: list[tuple[dict, list[str], str]]
) -> list[tuple[dict, str]]:
    """Two different color entries can both reduce to the same short SRS
    school name when the *actual* longer school (e.g. "Alabama State") isn't
    present in the SRS data at all -- both "Alabama Crimson Tide" and
    "Alabama State Hornets" then falsely tie for "Alabama". Prefer the entry
    whose next word looks like a mascot rather than an institution-name
    continuation; if that doesn't leave exactly one candidate, drop all of
    them rather than guess."""
    by_school: dict[str, list[tuple[dict, list[str]]]] = {}
    for entry, source_words, school in entry_school_pairs:
        by_school.setdefault(school, []).append((entry, source_words))
    resolved = []
    dropped = []
    for school, candidates in by_school.items():
        if len(candidates) == 1:
            resolved.append((candidates[0][0], school))
            continue
        school_len = len(normalize_words(school))
        likely = [
            (entry, words)
            for entry, words in candidates
            if len(words) <= school_len
            or words[school_len] not in INSTITUTION_CONTINUATION_WORDS
        ]
        if len(likely) == 1:
            resolved.append((likely[0][0], school))
        else:
            dropped.extend(entry["name"] for entry, _ in candidates)
    if dropped:
        print(f"  skipping ambiguous matches: {dropped}", file=sys.stderr)
    return resolved


def hex_saturation(hex_color: str) -> float:
    """HSL saturation, 0-1. Used only to pick which of a team's real listed
    colors reads as most "iconic" (e.g. Alabama's crimson over its grey
    trim) -- never to invent a color that isn't in the source data."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return 0.0
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    mx, mn = max(r, g, b), min(r, g, b)
    if mx == mn:
        return 0.0
    light = (mx + mn) / 2
    return (mx - mn) / (1 - abs(2 * light - 1))


def pick_primary_secondary(colors: list[str]) -> tuple[str, str]:
    non_neutral = [c for c in colors if c.upper() not in ("#FFFFFF", "#FFF", "#000000", "#000")]
    # Most saturated first, ties broken by original (source-listed) order.
    ordered = sorted(non_neutral, key=lambda c: -hex_saturation(c))
    ordered += [c for c in colors if c not in non_neutral]
    primary = ordered[0] if ordered else (colors[0] if colors else "")
    secondary = ordered[1] if len(ordered) > 1 else primary
    return primary, secondary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    source = fetch_source(args.refresh)
    srs_schools = load_srs_schools()
    srs_school_words = {s: normalize_words(s) for s in srs_schools}

    entry_school_pairs = []
    forced = []
    for entry in source:
        override = NAME_OVERRIDES.get(entry["name"])
        if override is not None:
            if override in srs_schools:
                forced.append((entry, override))
            continue
        source_words = normalize_words(entry["name"])
        school = best_match(source_words, srs_school_words)
        if school is not None:
            entry_school_pairs.append((entry, source_words, school))

    resolved = resolve_collisions(entry_school_pairs) + forced

    matches = []
    for entry, school in resolved:
        primary, secondary = pick_primary_secondary(entry.get("colors", []))
        matches.append(
            {
                "school": school,
                "primary_hex": primary,
                "secondary_hex": secondary,
                "matched_name": entry["name"],
            }
        )

    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["school", "primary_hex", "secondary_hex", "matched_name"]
        )
        writer.writeheader()
        writer.writerows(matches)

    print(
        f"Matched {len(matches)} of {len(source)} source entries "
        f"({len(srs_schools)} distinct SRS schools). Wrote {OUTPUT_CSV}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
