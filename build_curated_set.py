#!/usr/bin/env python3
"""
Build the "curated" team set: the ~150 programs that get a darker grey,
pick-able line by default in the front end. Everyone else (~250 more D-I
programs) still plots in the fainter grey background field and stays
hover-able -- they're just not emphasized up front, so the opening view is
readable instead of a 360-line tangle.

A program is in the curated set if ANY of these hold (all derived from
srs_ratings.csv + championships.csv -- nothing is hand-listed here):

  1. Its most recent season was played in a current high-major conference
     (ACC, Big Ten, Big 12, SEC, Big East -- SR conf slugs below).
  2. It ever finished a season ranked in the AP Top 25 final poll
     (only if srs_ratings.csv carries an `ap_rank` column with values).
  3. It ever finished a season in the SRS Top 25 for that year -- a fully
     reproducible, SRS-native stand-in that catches the historically
     strong mid-majors (1950s San Francisco, 1970s UNLV, Gonzaga, etc.)
     regardless of whether AP data is available.
  4. It ever won the national championship.

Output (written next to this script):
  curated_teams.csv - school, reasons (semicolon-joined)

Stdlib only. No network access -- reads the two CSVs the other build
scripts produce.
"""

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
SRS_CSV = HERE / "srs_ratings.csv"
CHAMPS_CSV = HERE / "championships.csv"
OUTPUT_CSV = HERE / "curated_teams.csv"

# Current high-major conferences, by Sports-Reference conference slug.
POWER_CONF_SLUGS = {"acc", "big-ten", "big-12", "sec", "big-east"}
# Fallback match on the abbreviation text, in case slugs aren't populated.
POWER_CONF_ABBRS = {"ACC", "Big Ten", "Big 12", "SEC", "Big East"}

TOP_N = 25


def load_srs() -> list[dict]:
    if not SRS_CSV.exists():
        sys.exit(f"{SRS_CSV.name} not found -- run build_srs.py first.")
    with SRS_CSV.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["year"] = int(r["year"])
        try:
            r["srs_val"] = float(r["srs"])
        except (ValueError, KeyError):
            r["srs_val"] = None
        try:
            r["ap"] = int(r["ap_rank"])
        except (ValueError, KeyError):
            r["ap"] = None
    return rows


def main():
    argparse.ArgumentParser().parse_args()
    rows = load_srs()

    by_school = defaultdict(list)
    for r in rows:
        by_school[r["school"]].append(r)

    # Reason 3: per-year SRS top 25.
    ever_top_srs = set()
    by_year = defaultdict(list)
    for r in rows:
        if r["srs_val"] is not None:
            by_year[r["year"]].append(r)
    for year, yr_rows in by_year.items():
        yr_rows.sort(key=lambda r: r["srs_val"], reverse=True)
        for r in yr_rows[:TOP_N]:
            ever_top_srs.add(r["school"])

    have_ap = any(r["ap"] is not None for r in rows)

    champs = set()
    if CHAMPS_CSV.exists():
        with CHAMPS_CSV.open(encoding="utf-8") as f:
            champs = {r["school"] for r in csv.DictReader(f)}

    curated = {}
    for school, srows in by_school.items():
        srows.sort(key=lambda r: r["year"])
        latest = srows[-1]
        reasons = []
        if latest.get("conf_slug") in POWER_CONF_SLUGS or latest.get("conf") in POWER_CONF_ABBRS:
            reasons.append("power-conf")
        if have_ap and any(r["ap"] is not None and r["ap"] <= 25 for r in srows):
            reasons.append("ever-AP")
        if school in ever_top_srs:
            reasons.append("ever-top25-SRS")
        if school in champs:
            reasons.append("champion")
        if reasons:
            curated[school] = reasons

    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["school", "reasons"])
        for school in sorted(curated):
            w.writerow([school, ";".join(curated[school])])

    print(f"Curated set: {len(curated)} of {len(by_school)} programs.", file=sys.stderr)
    if not have_ap:
        print("Note: srs_ratings.csv has no populated ap_rank column; "
              "'ever-AP' rule skipped (SRS-top-25 rule still applies).", file=sys.stderr)
    print(f"Wrote {OUTPUT_CSV}", file=sys.stderr)


if __name__ == "__main__":
    main()
