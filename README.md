# NFL practice report archive

A daily capture of the NFL's published practice participation report, kept so
the **progression across the week** survives.

Unofficial. Not affiliated with, endorsed by, or sponsored by the National
Football League or any club.

## Why this exists

The practice report is a league-mandated disclosure: clubs report Wednesday,
Thursday and Friday participation, plus a final game status. It is published at
`nfl.com/injuries` as one table with a single **Practice Status** column.

That column is **overwritten each day**. It shows Wednesday's participation on
Wednesday and Thursday's on Thursday. The archived week pages carry the same
five columns, so they preserve the final state, not the three days. Every
public dataset built on that page inherits the limitation — nflverse, for
instance, stores one row per player-week.

So the *shape* of a player's week is not published by anyone as a retrievable
series. And the shape is the part that carries information:

| week | reading |
|---|---|
| DNP → Limited → Full | arriving; trending toward playing |
| Full → Limited → DNP | leaving; something got worse |
| Limited → Limited → Limited | managed, no news |

Both of the first two collapse to a single status everywhere else.

**A day not captured is gone from every source on earth.** That is the whole
reason this repository exists: the data cannot be backfilled, only accumulated.

## What is here

```
data/                 one JSON snapshot per capture
data/raw/             the gzipped page each snapshot was parsed from
progression/          Wed/Thu/Fri series assembled from the snapshots
tools/                the collector and the assembler
tests/                unit tests for both
```

Captures run Wednesday, Thursday and Friday, twice each day — clubs file
through the afternoon and amend, and west-coast clubs file late. When two
captures land on the same day the later one wins, because it is a correction.

**A capture's date is not its practice day.** The second run of each day fires
in the evening local time, which is the *following* date in UTC, and the page
carries one Practice Status column that is only overwritten once the clubs
file. So the assembler decides a capture's day from the clock: anything taken
before 15:00 UTC — before any club in the league has filed — belongs to the day
before, and is listed in `snapshots_reattributed`. A capture that restates the
previous day unchanged is ignored entirely (`snapshots_ignored_as_stale`) and
its day is reported MISSING, because a page nobody has touched is not a filing.

**One week at a time.** The columns are named by weekday, so a second week on
disk fits into them without complaint — and until 2026-09-18 it did exactly
that. The assembler now builds a single practice week, Wednesday-anchored,
named in `week_of`; captures from any other week are listed in
`snapshots_excluded_other_weeks` rather than merged. That matters most on the
day a week is still filling: with Wednesday and Thursday captured and no Friday
yet, the old behaviour dropped the *previous* Friday into the Friday column and
reported `days_missing: []`. It also carried players forward who had not been
reported at all that week, showing last week's three days as this week's. Build
an earlier week with `--week-of YYYY-MM-DD`.

Each column also carries the capture that fed it, in `day_captured_at`. A
consumer timestamping these filings should read that map and not infer times
from the order of `snapshots_used`.

## Using it

```bash
python tools/nfl_injury_report.py            # capture today
python tools/practice_progression.py --csv progression.csv
```

Practice status is graded `DNP` / `LIMITED` / `FULL`, with the verbatim filing
kept alongside.

## What the data does not tell you

`FULL` means the club filed *Full Participation*. It does not mean there was no
snap limit, no pitch count, and no management. Those are real and are not in
this filing. Treat participation as one input, not a verdict.

A player absent from a capture is only healthy if his club had filed at that
moment — every snapshot carries `clubs_filed_list` so you can tell the
difference between "no report" and "not reported yet".

## Design note

The fetch is unrecoverable; the parse is not. So the raw page is archived
**before** parsing, and archived even when parsing then fails — that is exactly
the run whose bytes you want. `--from-raw` re-parses any archived page, which
makes every stored day a regression case for a future parser fix.

The collector fails closed. It counts participation strings in the raw HTML and
refuses to write if fewer reached a record, because a practice report that
silently drops players reads as good news about them.

## Source and license

Data derived from publicly published NFL injury reports at
<https://www.nfl.com/injuries/>. Code and compiled data in this repository are
released under [CC BY 4.0](LICENSE) — use it, attribute it.

## Scope: this repository publishes the NFL filing and nothing else

The practice report is a league-mandated disclosure, and who practised at what
level is a fact. Facts are not copyrightable, and the filing is published
precisely so it is public.

Other sources of player health — beat reporting, aggregator news bodies,
analysts' writing — are **not** republished here, in whole or in part. That is
somebody's work, and reading it is a different act from redistributing it. Any
tooling that consumes this archive alongside those sources should keep them on
its own side of the line.
