#!/usr/bin/env python3
"""
Build a year-by-year NCAA men's basketball SRS (Simple Rating System) table,
1950-present (SR's SRS data starts with the 1949-50 season).

Output (written next to this script):
  srs_ratings.csv  - one row per team-season: year, school, school_slug,
                      conf, conf_slug, ap_rank, wins, losses, srs_off,
                      srs_def, srs

Source
------
Sports-Reference/CBB "ratings" page per season. SR uses two URL shapes over
the site's history:
    https://www.sports-reference.com/cbb/seasons/men/{year}-ratings.html   (recent)
    https://www.sports-reference.com/cbb/seasons/{year}-ratings.html       (legacy)
`year` is the season's *end* year -- 2024 == the 2023-24 season.

Sports-Reference sits behind a Cloudflare JS challenge that blocks plain
HTTP scraping, and as a small ad-supported site has asked not to be treated
as an API. So this script never talks to sports-reference.com directly --
it pulls the same pages out of the Internet Archive Wayback Machine, which
SR has no objection to and which puts zero load on their servers.
See https://web.archive.org.

Nothing in this file is typed from memory. The ratings table is parsed
directly out of archived HTML; nothing here hardcodes a team's rating.

Stdlib only. Downloads cache into data/; pass --refresh to re-download.
Be polite to archive.org too: this sleeps between requests by default.
"""

import argparse
import csv
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
RAW_DIR = DATA_DIR / "raw"
CDX_DIR = DATA_DIR / "cdx"
OUTPUT_CSV = HERE / "srs_ratings.csv"

FIRST_YEAR = 1950
LAST_YEAR = 2026

# Two URL shapes; try the modern one first, fall back to legacy.
SR_PATHS = (
    "cbb/seasons/men/{year}-ratings.html",
    "cbb/seasons/{year}-ratings.html",
)
CDX_TMPL = (
    "https://web.archive.org/cdx/search/cdx"
    "?url=sports-reference.com/{path}"
    "&output=json&filter=statuscode:200&collapse=digest"
)
# The Wayback "availability" API is a single fast lookup for the snapshot
# closest to a timestamp. A far-future timestamp yields the most recent
# snapshot. It is far more reliable than the CDX search endpoint, which is
# frequently overloaded and can take well over a minute to answer (or time
# out entirely). CDX stays as a fallback below.
AVAIL_TMPL = (
    "https://archive.org/wayback/available?url={url}&timestamp=20991231235959"
)
SNAPSHOT_TMPL = "https://web.archive.org/web/{timestamp}if_/{original}"
SR_URL_TMPL = "https://www.sports-reference.com/{path}"

USER_AGENT = "NCAABSRSTimeline research script (contact: ecg2104@columbia.edu)"
REQUEST_DELAY_SECONDS = 1.5
MAX_RETRIES = 5
BACKOFF_CAP_SECONDS = 60
# archive.org endpoints routinely take 30-60s to respond under load, so the
# read timeout has to be generous or every request "times out" on the first
# byte and the run collects nothing.
REQUEST_TIMEOUT_SECONDS = 90


def fetch(url: str) -> bytes:
    """GET with retries. The Wayback Machine is prone to multi-day outages and,
    while recovering, to intermittent connection timeouts, read timeouts and
    HTTP 5xx -- all of which are worth retrying with exponential backoff. A 404
    is definitive (no such snapshot), so it's raised immediately without
    burning retries."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            last_err = e
        except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
            # A read timeout on resp.read() surfaces as TimeoutError, which is
            # NOT a subclass of URLError -- without this branch the most common
            # archive.org failure mode skips the retry loop entirely.
            last_err = e
        if attempt < MAX_RETRIES:
            wait = min(REQUEST_DELAY_SECONDS * (2 ** (attempt - 1)), BACKOFF_CAP_SECONDS)
            print(f"    retry {attempt}/{MAX_RETRIES - 1} in {wait:.0f}s ({last_err})", file=sys.stderr)
            time.sleep(wait)
    raise last_err


def get_snapshot_timestamp_via_availability(path: str, key: str) -> str | None:
    """Fast single-request lookup of the most recent snapshot's timestamp.
    Returns None (rather than raising) on any failure, so the CDX fallback
    can take over."""
    original = SR_URL_TMPL.format(path=path)
    url = AVAIL_TMPL.format(url=urllib.parse.quote(original, safe=""))
    try:
        raw = fetch(url)
    except Exception as e:
        print(f"  [{key}] availability lookup failed: {e}", file=sys.stderr)
        return None
    time.sleep(REQUEST_DELAY_SECONDS)
    try:
        snap = json.loads(raw).get("archived_snapshots", {}).get("closest")
    except (ValueError, AttributeError):
        return None
    if snap and snap.get("available") and str(snap.get("status")) == "200":
        return snap.get("timestamp")
    return None


def get_snapshot_timestamp(path: str, key: str, refresh: bool) -> str | None:
    CDX_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CDX_DIR / f"{key}.json"
    if cache_file.exists() and not refresh:
        entries = json.loads(cache_file.read_text())
    else:
        url = CDX_TMPL.format(path=path)
        try:
            raw = fetch(url)
        except Exception as e:
            print(f"  [{key}] CDX lookup failed: {e}", file=sys.stderr)
            return None
        time.sleep(REQUEST_DELAY_SECONDS)
        entries = json.loads(raw.decode("utf-8"))
        cache_file.write_text(json.dumps(entries))
    if len(entries) < 2:
        return None
    # entries[0] is the header row; take the most recent snapshot (last row).
    return entries[-1][1]


def get_page_html(year: int, refresh: bool) -> str | None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = RAW_DIR / f"{year}.html"
    if cache_file.exists() and not refresh:
        return cache_file.read_text(encoding="utf-8", errors="ignore")

    for i, path_tmpl in enumerate(SR_PATHS):
        path = path_tmpl.format(year=year)
        key = f"{year}" if i == 0 else f"{year}-legacy"
        timestamp = get_snapshot_timestamp_via_availability(path, key)
        if timestamp is None:
            # availability API had nothing (or errored) -- fall back to CDX
            timestamp = get_snapshot_timestamp(path, key, refresh)
        if timestamp is None:
            continue
        snapshot_url = SNAPSHOT_TMPL.format(
            timestamp=timestamp, original=SR_URL_TMPL.format(path=path)
        )
        try:
            raw = fetch(snapshot_url)
        except Exception as e:
            print(f"  [{year}] snapshot fetch failed ({key}): {e}", file=sys.stderr)
            continue
        time.sleep(REQUEST_DELAY_SECONDS)
        html = raw.decode("utf-8", errors="ignore")
        cache_file.write_text(html, encoding="utf-8")
        return html

    print(f"  [{year}] no archived snapshot found (tried both URL shapes)", file=sys.stderr)
    return None


SLUG_RE = re.compile(r"/cbb/schools/([^/]+)/|/cbb/conferences/([^/]+)/")

# data-stat names vary a little across SR's own history; try each in order.
FIELD_CANDIDATES = {
    "school": ["school_name"],
    "conf": ["conf_abbr", "conf_id"],
    "ap_rank": ["rank_final", "ap_rank", "wins_ncaa"],  # usually absent; harmless if so
    "wins": ["wins"],
    "losses": ["losses"],
    "srs_off": ["srs_off", "osrs", "off_srs"],
    "srs_def": ["srs_def", "dsrs", "def_srs"],
    "srs": ["srs"],
}

SEEN_STATS: set[str] = set()


class RatingsTableParser(HTMLParser):
    """Pulls rows out of <table id="ratings"> keyed by each cell's data-stat."""

    def __init__(self):
        super().__init__()
        self.in_table = False
        self.table_depth = 0
        self.in_row = False
        self.in_cell = False
        self.cell_stat = None
        self.cell_href = None
        self.cell_text = []
        self.current_row = {}
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table" and attrs.get("id") == "ratings":
            self.in_table = True
            self.table_depth = 1
            return
        if not self.in_table:
            return
        if tag == "table":
            self.table_depth += 1
        elif tag == "tr":
            self.in_row = True
            self.current_row = {}
        elif tag in ("td", "th") and self.in_row:
            self.in_cell = True
            self.cell_stat = attrs.get("data-stat")
            if self.cell_stat:
                SEEN_STATS.add(self.cell_stat)
            self.cell_href = None
            self.cell_text = []
        elif tag == "a" and self.in_cell:
            href = attrs.get("href", "")
            m = SLUG_RE.search(href)
            if m:
                self.cell_href = m.group(1) or m.group(2)

    def handle_endtag(self, tag):
        if not self.in_table:
            return
        if tag == "table":
            self.table_depth -= 1
            if self.table_depth == 0:
                self.in_table = False
        elif tag in ("td", "th") and self.in_cell:
            text = "".join(self.cell_text).strip()
            if self.cell_stat:
                self.current_row[self.cell_stat] = text
                if self.cell_href:
                    self.current_row[self.cell_stat + "_slug"] = self.cell_href
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            name = self.current_row.get("school_name")
            if name and name not in ("School", ""):
                self.rows.append(self.current_row)
            self.in_row = False

    def handle_data(self, data):
        if self.in_cell:
            self.cell_text.append(data)


def _pick(row: dict, keys: list[str]) -> str:
    for k in keys:
        if row.get(k):
            return row[k]
    return ""


def canonical_name_by_slug(rows: list[dict]) -> dict[str, str]:
    """Sports Reference keeps a school's URL slug stable for life but has *not*
    kept its display name stable:
      * around 2022 it restyled compound names from a hyphen to an en dash
        ("Nevada-Las Vegas" -> "Nevada–Las Vegas"), so old and new
        snapshots disagree;
      * it shows a program under whatever name it currently uses, so a rebrand
        (IUPUI -> IU Indy, Central Florida -> UCF, McNeese State -> McNeese)
        splits one program's line in two.
    The slug is the real identity key. Collapse every slug to a single display
    name: the one Sports Reference uses in that program's most recent season
    (i.e. its current name -- "NC State", not "North Carolina State"; "UCF",
    not "Central Florida"). Dashes are normalised to a plain hyphen first, so
    the ~2022 hyphen-to-en-dash restyle doesn't leak an en dash into the
    "current" name.
    """
    latest_year: dict[str, int] = {}
    canon: dict[str, str] = {}
    for r in rows:
        slug = r["school_slug"]
        if not slug:
            continue
        name = r["school"].replace("—", "-").replace("–", "-")
        y = int(r["year"])
        if slug not in latest_year or y >= latest_year[slug]:
            latest_year[slug] = y
            canon[slug] = name
    return canon


def parse_year(year: int, html: str) -> list[dict]:
    parser = RatingsTableParser()
    parser.feed(html)
    out = []
    for row in parser.rows:
        out.append(
            {
                "year": year,
                "school": _pick(row, FIELD_CANDIDATES["school"]),
                "school_slug": row.get("school_name_slug", ""),
                "conf": _pick(row, FIELD_CANDIDATES["conf"]),
                "conf_slug": row.get("conf_abbr_slug", "") or row.get("conf_id_slug", ""),
                "ap_rank": _pick(row, FIELD_CANDIDATES["ap_rank"]),
                "wins": _pick(row, FIELD_CANDIDATES["wins"]),
                "losses": _pick(row, FIELD_CANDIDATES["losses"]),
                "srs_off": _pick(row, FIELD_CANDIDATES["srs_off"]),
                "srs_def": _pick(row, FIELD_CANDIDATES["srs_def"]),
                "srs": _pick(row, FIELD_CANDIDATES["srs"]),
            }
        )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download cached pages")
    ap.add_argument("--start", type=int, default=FIRST_YEAR)
    ap.add_argument("--end", type=int, default=LAST_YEAR)
    args = ap.parse_args()

    all_rows = []
    consecutive_misses = 0
    for year in range(args.start, args.end + 1):
        print(f"[{year}] fetching...", file=sys.stderr)
        html = get_page_html(year, args.refresh)
        if html is None:
            consecutive_misses += 1
            # A handful of individual years may genuinely have no snapshot, but
            # a long unbroken run of misses means the Wayback Machine is down
            # (see the recurring multi-day archive.org outages). Bail early with
            # a clear message rather than grinding through 70 more timeouts;
            # re-running later picks up every year from the data/ cache.
            if consecutive_misses >= 6 and not all_rows:
                print(
                    "\nAborting: 6 consecutive years failed to fetch and nothing "
                    "has downloaded. The Wayback Machine (web.archive.org) is "
                    "almost certainly down or degraded -- check "
                    "https://web.archive.org/ and re-run when it responds. "
                    "Progress is cached, so a later run resumes where this left off.",
                    file=sys.stderr,
                )
                sys.exit(2)
            continue
        consecutive_misses = 0
        rows = parse_year(year, html)
        print(f"  [{year}] {len(rows)} teams", file=sys.stderr)
        all_rows.extend(rows)

    if not all_rows:
        print("No data collected.", file=sys.stderr)
        sys.exit(1)

    # Surface the actual data-stat names seen, so the FIELD_CANDIDATES map can
    # be corrected if SR's schema differs from what's assumed above.
    print(f"\nObserved data-stat names: {sorted(SEEN_STATS)}", file=sys.stderr)
    no_srs = sum(1 for r in all_rows if not r.get("srs"))
    if no_srs:
        print(
            f"WARNING: {no_srs} of {len(all_rows)} rows have no SRS value -- "
            "check FIELD_CANDIDATES against the observed data-stat names above.",
            file=sys.stderr,
        )

    # Collapse each program (by stable slug) to one display name, and report
    # every row whose name changes so the mapping can be eyeballed.
    canon = canonical_name_by_slug(all_rows)
    renames: dict[str, str] = {}
    for r in all_rows:
        slug = r["school_slug"]
        new = canon.get(slug)
        if new and new != r["school"]:
            renames[r["school"]] = new
            r["school"] = new
    if renames:
        print(f"\nName canonicalisation ({len(renames)} spellings collapsed):", file=sys.stderr)
        for old, new in sorted(renames.items()):
            print(f"  {old!r} -> {new!r}", file=sys.stderr)

    fieldnames = [
        "year", "school", "school_slug", "conf", "conf_slug",
        "ap_rank", "wins", "losses", "srs_off", "srs_def", "srs",
    ]
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"Wrote {len(all_rows)} rows to {OUTPUT_CSV}", file=sys.stderr)


if __name__ == "__main__":
    main()
