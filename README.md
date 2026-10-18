# NCAAB SRS Timeline

An interactive timeline of men's college basketball team strength, using the
Simple Rating System (SRS) from [Sports Reference](https://www.sports-reference.com/cbb/),
1950&ndash;2026, plus the data pipeline behind it.

This is the basketball companion to
[NCAAFSRSTimeline](https://github.com/ssitari/NCAAFSRSTimeline). The design is
lifted wholesale from the old FiveThirtyEight "Complete History of the NBA"
Elo charts (desaturated background field, saturate-on-select, per-team color,
championship call-outs). Basketball has ~360 Division I teams &mdash; far too
many for the football version's "plot everyone in grey" opening &mdash; so
this version draws a **curated ~150-team set** in a darker resting grey and
leaves the rest in a fainter background field. Any line takes its team color
on hover or pin.

## Live demo

[View on GitHub Pages](https://ssitari.github.io/NCAABSRSTimeline/)

## Why SRS

SRS is a team's average scoring margin adjusted for strength of schedule,
denominated in points above/below that season's average (zero = an average
team). Because it's re-centered on the mean every year, trajectories from
different eras are directly comparable without extra normalization.
Sports-Reference's explanation:
[glossary](https://www.sports-reference.com/cbb/about/glossary.html).

## The curated set

`build_curated_set.py` decides which programs get a darker, pick-able line
by default. A program qualifies if **any** of these hold &mdash; all derived
from the data files, nothing hand-listed:

1. Its most recent season was in a current high-major conference (ACC, Big
   Ten, Big 12, SEC, Big East).
2. It ever finished a season ranked in the AP Top 25 &mdash; *only if*
   `srs_ratings.csv` carries a populated `ap_rank` column (SR's ratings page
   historically does not, so this rule is usually inactive).
3. It ever finished a season in the **SRS Top 25** for that year. This is a
   fully reproducible, SRS-native stand-in for "was nationally relevant" that
   catches the strong non-power programs (1950s San Francisco, 1970s&ndash;90s
   UNLV, Gonzaga, Memphis, etc.) regardless of AP data availability.
4. It ever won the national championship.

Everything else still plots in the grey background field and stays
hover-able &mdash; it's just not competing for attention on load. The
"High-major / ranked teams only" toggle hides the grey field entirely.

## Data pipeline

Run in this order (each writes a CSV next to the script; `data/` caches
downloads and is gitignored):

```
python3 build_srs.py            # SRS ratings, 1950-2026, from SR via Wayback
python3 build_team_colors.py    # real team colors, cross-referenced
python3 build_championships.py  # NCAA tournament champions, from Wikipedia
python3 build_curated_set.py    # derives curated_teams.csv from the above
```

### `build_srs.py`

SR publishes a per-season ratings page under two URL shapes over the site's
history (`cbb/seasons/men/{year}-ratings.html` and the older
`cbb/seasons/{year}-ratings.html`); `year` is the season *end* year. SR sits
behind a Cloudflare challenge and has asked not to be treated as an API, so
this never contacts sports-reference.com directly &mdash; it pulls the same
archived pages from the [Wayback Machine](https://web.archive.org), which
puts zero load on SR.

SR's own column names (`data-stat` attributes) have drifted over the years,
so the parser keeps every cell and maps fields by a candidate list; on each
run it prints the actual `data-stat` names it saw and warns if any rows came
back without an SRS value, so `FIELD_CANDIDATES` can be corrected.

SR keeps a school's URL slug stable for life but not its display name &mdash;
it restyled compound names from a hyphen to an en dash around 2022, and it
shows programs under their current name after a rebrand (IUPUI &rarr; IU Indy,
Central Florida &rarr; UCF). Left alone that splits one program's line in
two, so the slug is treated as the identity key: every slug is collapsed to
the name it carried in its most recent season, dashes normalised to a plain
hyphen. Each run prints the full list of spellings it merged. A season a
program spent as a Division I *transitional* member has a win-loss record but
no SR rating; those rows are kept with an empty `srs` and the front end
breaks the line there.

The Wayback lookups use the fast [availability API](https://archive.org/help/wayback_api.php)
(most-recent snapshot in one request), falling back to the CDX search
endpoint only when that comes up empty; read timeouts and 5xx are retried
with backoff.

Downloads cache in `data/` (gitignored &mdash; not redistributed, per SR's
request). Only the derived `srs_ratings.csv` is committed. `--start`/`--end`
writes the whole CSV for that range only; to backfill, delete the specific
cache files and re-run the full range.

### `build_championships.py`

Parses the "National championship games" table from Wikipedia's
[List of NCAA Division I men's basketball champions](https://en.wikipedia.org/wiki/List_of_NCAA_Division_I_men%27s_basketball_champions).
One row per season, 1939&ndash;present (2020 skipped &mdash; tournament
cancelled). Cells are identified by content, not column position, because
back-to-back same-coach champions (UCLA's Wooden run, Duke 1991&ndash;92,
Florida 2006&ndash;07, &hellip;) rowspan the coach cell away.

2013 Louisville is kept with `vacated=True` (won on the court, title later
stripped by the NCAA) rather than dropped; the front end draws its marker
hollow. No retroactive pre-1939 Helms/Premo-Porretta selections &mdash; this
is the clean, one-champion-per-year tournament record only.

Champion names are matched to `srs_ratings.csv`'s `school` column by exact
match plus a small alias table (Wikipedia's link labels differ &mdash;
"UConn" vs "Connecticut", "Loyola Chicago" vs "Loyola (IL)"); every unmatched
name is printed so the table can be completed after the first full SRS run.

### `build_team_colors.py`

Same as the football repo: cross-references schools against
[glidej/ncaa-team-colors](https://github.com/glidej/ncaa-team-colors) (real
sourced hex colors for current NCAA programs) by a whitespace-boundary prefix
match, with a hand-maintained `NAME_OVERRIDES` table for the cases the prefix
match can't bridge (abbreviations like "LSU"/"USC"/"UNLV", parenthetical
qualifiers, the Virginia/VMI prefix collision). The colors themselves always
come straight from the source JSON; the override only fixes which school they
attach to. Unmatched / defunct programs (CCNY, NYU, Oklahoma City, &hellip;)
fall back to a conference-derived color in the front end rather than a
guessed one.

## The visualization

`index.html` / `app.js` / `config.js` &mdash; `config.js` is the only file
to edit when adapting the tool; `app.js` is the engine. Serve over HTTP
(`python3 -m http.server`, `npx serve .`) &mdash; it loads the CSVs via
`fetch()` and won't run from a `file://` URL.

- Curated teams plot in one uniform darker grey, non-curated teams in a
  fainter grey; hovering any line shows it in its team color;
  the sidebar search boxes pin teams or conferences (dashed = conference
  per-year average SRS).
- The context strip at the bottom brushes/zooms both axes; axis domains
  follow the curated set only, so one outlier season from an otherwise
  ignored program can't stretch the scale.
- "5-yr moving avg" smooths every line. "High-major / ranked teams only"
  drops the grey field.
- Gold markers on a pinned/hovered line are that team's NCAA titles (hollow
  = vacated); hover one for the final and coach.
- The sidebar reports the top 10 programs and every conference's average SRS
  for the visible range, gated on presence in at least half the visible
  seasons.

## Acknowledgements

Design directly inspired by FiveThirtyEight's "Complete History of the NBA"
interactive &mdash; RIP.

SRS ratings: [Sports-Reference/CBB](https://www.sports-reference.com/cbb/),
retrieved via the [Internet Archive Wayback Machine](https://web.archive.org).
Championships: [Wikipedia](https://en.wikipedia.org/wiki/List_of_NCAA_Division_I_men%27s_basketball_champions).
Team colors: [glidej/ncaa-team-colors](https://github.com/glidej/ncaa-team-colors).

Most of the code written with assistance from [Claude](https://claude.ai) (Anthropic).
