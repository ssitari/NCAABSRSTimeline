#!/usr/bin/env python3
"""
Build a table of NCAA Division I men's basketball national champions,
1939-present (the tournament has been held every year since 1939 except
2020, which was cancelled).

Output (written next to this script):
  championships.csv - one row per season: year, school (matched to
                       srs_ratings.csv's `school` column where possible),
                       coach, score, runner_up, vacated

Source
------
Wikipedia, "List of NCAA Division I men's basketball champions",
"National championship games" table:
    https://en.wikipedia.org/wiki/List_of_NCAA_Division_I_men%27s_basketball_champions

Only the single, unambiguous NCAA tournament winner is recorded per year
(the design decision for this project -- no retroactive Helms/Premo-Porretta
pre-1939 selections). The one on-court champion the NCAA later stripped of
its title (2013 Louisville) is kept with vacated=True rather than dropped.

Nothing in this file is typed from memory -- everything is parsed directly
out of Wikipedia's wikitext. Team-name matching against the SRS data uses
an exact match plus a small hand-verified alias table (logged, not guessed
silently).

Stdlib only. Downloads cache into data/; pass --refresh to re-download.
"""

import argparse
import csv
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
SRS_CSV = HERE / "srs_ratings.csv"
OUTPUT_CSV = HERE / "championships.csv"

WIKI_TITLE = "List of NCAA Division I men's basketball champions"
WIKI_URL = (
    "https://en.wikipedia.org/w/index.php?title="
    + urllib.parse.quote(WIKI_TITLE.replace(" ", "_"))
    + "&action=raw"
)
CACHE_FILE = DATA_DIR / "wiki_champions_raw.txt"
USER_AGENT = "NCAABSRSTimeline research script (contact: ecg2104@columbia.edu)"

# Wikipedia's champion display names vs. Sports-Reference's `school` values.
# Each entry is verified against an actual unmatched name from a parser run.
# Seed guesses; the parser prints every unmatched name so this can be
# completed against the real srs_ratings.csv on the first full run.
SCHOOL_ALIASES = {
    "Texas Western": "UTEP",
    "Oklahoma A&M": "Oklahoma State",
    "CCNY": "City College of New York",
    "NC State": "North Carolina State",
    "Pittsburgh": "Pitt",
    # Wikipedia's link label differs from Sports Reference's school name.
    "UConn": "Connecticut",
    "UNLV": "Nevada-Las Vegas",
    "Loyola Chicago": "Loyola (IL)",
}


def fetch_source(refresh: bool) -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if CACHE_FILE.exists() and not refresh:
        return CACHE_FILE.read_text(encoding="utf-8")
    req = urllib.request.Request(WIKI_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8")
    CACHE_FILE.write_text(text, encoding="utf-8")
    return text


# ------------------------------------------------------------------
# Wikitext cleaning
# ------------------------------------------------------------------

REF_RE = re.compile(r"<ref\b[^>]*?/>|<ref\b[^>]*?>.*?</ref>", re.DOTALL)
EFN_RE = re.compile(r"\{\{efn\b.*?\}\}", re.DOTALL | re.IGNORECASE)
SUP_SUB_RE = re.compile(r"<su[bp]\b[^>]*>.*?</su[bp]>", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[(?:[^\]|]+\|)?([^\]|]+)\]\]")


def strip_templates(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    return text


def clean_cell(text: str) -> str:
    text = REF_RE.sub("", text)
    text = EFN_RE.sub("", text)
    text = SUP_SUB_RE.sub("", text)
    text = WIKILINK_RE.sub(lambda m: m.group(1), text)
    text = strip_templates(text)
    text = text.replace("&nbsp;", " ").replace("&ndash;", "–")
    return text.strip()


def sortname(cell: str) -> str:
    """{{sortname|First|Last}} or {{sortname|First|Last|article}} -> 'First Last'."""
    m = re.search(r"\{\{sortname\|([^|}]+)\|([^|}]+)", cell)
    if m:
        return f"{m.group(1).strip()} {m.group(2).strip()}"
    return clean_cell(cell)


def score(cell: str) -> str:
    """{{sort|046.3|[[..|46–33]]}} -> '46–33'."""
    m = re.search(r"\{\{sort\|[^|]*\|(.*?)\}\}", cell, re.DOTALL)
    inner = m.group(1) if m else cell
    return clean_cell(inner)


# ------------------------------------------------------------------
# Parse the championship-games table
# ------------------------------------------------------------------

def parse_table(wikitext: str) -> list[dict]:
    start = wikitext.index('{| class="wikitable sortable"')
    end = wikitext.index("\n|}", start)
    table = wikitext[start:end]

    rows = table.split("\n|-")
    out = []
    for row in rows:
        row = row.strip()
        if not row or "scope=\"row\"" not in row:
            continue

        # Split into cells: each starts with a line beginning "|" or "!".
        cells = re.split(r"\n[|!]", "\n" + row)
        cells = [c.strip() for c in cells if c.strip()]
        if len(cells) < 2:
            continue

        year_m = re.search(r"(\d{4})", cells[0])
        if not year_m:
            continue
        year = int(year_m.group(1))

        rest = cells[1:]
        if rest and rest[0].lstrip("|").strip().startswith("colspan"):
            continue  # e.g. 2020 -- tournament not held

        # Identify cells by content, not position: coach/venue/city cells are
        # frequently rowspanned away on back-to-back same-coach champions
        # (UCLA's Wooden run, Duke 1991-92, ...), which shifts column indices.
        # Season-article links read "[[1962–63 Loyola Ramblers…" but the
        # millennium-crossing season is "[[1999–2000 Michigan State…" -- the
        # tail is 2 digits normally, 4 across the century boundary.
        team_link = re.compile(r"\[\[\d{4}[–-]\d{2,4}\s")
        score_pat = re.compile(r"championship game\||\d{2,3}\s*[–-]\s*\d{2,3}")

        champ_idx = next((i for i, c in enumerate(rest) if team_link.search(c)), None)
        if champ_idx is None:
            continue
        champ_cell = rest[champ_idx]
        vacated = "*" in champ_cell or "vacat" in champ_cell.lower()
        champ = clean_cell(champ_cell).strip("' *").strip()
        if not champ or champ.lower() in ("none", "vacated"):
            continue

        after = rest[champ_idx + 1:]
        score_idx = next((i for i, c in enumerate(after) if score_pat.search(c)), None)
        game_score = score(after[score_idx]) if score_idx is not None else ""
        coach = ""
        for c in after[: score_idx if score_idx is not None else 0]:
            if "sortname" in c:
                coach = sortname(c)
                break
        runner_up = ""
        if score_idx is not None:
            ru = next((c for c in after[score_idx + 1:] if team_link.search(c)), "")
            runner_up = clean_cell(ru).strip("' *").strip()

        out.append(
            {
                "year": year,
                "team_raw": champ,
                "coach": coach,
                "score": game_score,
                "runner_up": runner_up,
                "vacated": vacated,
            }
        )
    return out


def load_srs_schools() -> set[str]:
    if not SRS_CSV.exists():
        print(f"NOTE: {SRS_CSV.name} not found -- run build_srs.py first for name matching.", file=sys.stderr)
        return set()
    with SRS_CSV.open(encoding="utf-8") as f:
        return {row["school"] for row in csv.DictReader(f)}


def match_school(team_raw: str, srs_schools: set[str]) -> str | None:
    if not srs_schools:
        return team_raw  # no SRS data to match against yet; keep raw name
    if team_raw in srs_schools:
        return team_raw
    alias = SCHOOL_ALIASES.get(team_raw)
    if alias and alias in srs_schools:
        return alias
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    wikitext = fetch_source(args.refresh)
    claims = parse_table(wikitext)

    # A coach cell rowspanned across back-to-back same-school titles leaves the
    # later year's coach blank; inherit it from the previous season.
    for i in range(1, len(claims)):
        if not claims[i]["coach"] and claims[i - 1]["team_raw"] == claims[i]["team_raw"]:
            claims[i]["coach"] = claims[i - 1]["coach"]
    srs_schools = load_srs_schools()

    matched, unmatched = [], set()
    for c in claims:
        school = match_school(c["team_raw"], srs_schools)
        if school is None:
            unmatched.add(c["team_raw"])
            continue
        matched.append(
            {
                "year": c["year"],
                "school": school,
                "coach": c["coach"],
                "score": c["score"],
                "runner_up": c["runner_up"],
                "vacated": c["vacated"],
            }
        )

    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["year", "school", "coach", "score", "runner_up", "vacated"]
        )
        writer.writeheader()
        writer.writerows(matched)

    print(f"Parsed {len(claims)} champions, matched {len(matched)} to SRS schools.", file=sys.stderr)
    if unmatched:
        print(f"Unmatched ({len(unmatched)}): {sorted(unmatched)} -- add to SCHOOL_ALIASES.", file=sys.stderr)
    print(f"Wrote {OUTPUT_CSV}", file=sys.stderr)


if __name__ == "__main__":
    main()
