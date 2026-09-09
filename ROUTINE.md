# The capture routine

Runs on **GitHub Actions** — `.github/workflows/capture.yml`. No machine has to
be awake, no credentials are needed, and nothing is installed.

## Why Actions rather than anything cleverer

The capture is the only part of this with a deadline. `nfl.com/injuries` keeps
one `Practice Status` column and overwrites it daily, so a run that does not
happen loses that day from every source on earth. Every other failure here is
recoverable.

That makes availability the only thing worth optimising, and this job needs no
judgement at all — fetch a page, parse it, commit. Actions is free and
unlimited on public repos, holds write access to its own repo, and both tools
are stdlib-only on purpose: a collector with a dependency tree is one that can
fail to start on the single afternoon that matters.

## Schedule

```
47 21 * * 3,4,5     16:47 US/Central, Wed/Thu/Fri
47  1 * * 4,5,6     20:47 US/Central, Wed/Thu/Fri (next UTC day)
```

Twice a day because clubs file through the afternoon and amend, and west-coast
clubs file late. Measured on 2026-09-09: 4 clubs at 19:00 UTC, 6 clubs at 19:36
— they file while you watch. When two captures land on the same day the later
one wins, because it is a correction rather than a duplicate.

The two lines look asymmetric because Actions is UTC-only and the evening run
lands on the following UTC day. Under CST the local times drift an hour
earlier, which the wide window absorbs.

Trigger a run by hand any time with `gh workflow run capture.yml`, or the
Actions tab.

## Two deliberate behaviours

**It commits even when the parse fails.** The raw page is archived *before*
parsing, so a failed parse still leaves bytes worth keeping — and they can be
re-parsed later with `--from-raw`, but only if they were pushed.

**It then fails the run loudly.** A green tick on a day when nothing was
collected is the one outcome that loses data quietly, so the workflow exits
non-zero and GitHub emails you.

## What it will never do

It will not repair the parser. The collector counts participation strings in
the raw HTML and refuses to write if fewer reached a record; anything that
"adapts until it parses" is being asked to defeat that check, trading a loud
failure for confident wrong data on a feed where wrong reads as good news.

The safe form of repair works the other way round: fail closed, then fix
against `data/raw/` as a regression corpus — every day already captured has to
keep parsing identically.

## Running it by hand

```bash
python3 tools/nfl_injury_report.py
python3 tools/practice_progression.py --csv progression/progression.csv
git add -A data progression && git commit -m "Capture $(date +%F)" && git push
```
