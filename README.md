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
