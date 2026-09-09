# The capture routine

The capture must run without a laptop being awake, because a missed day cannot
be backfilled. This is the spec for a scheduled cloud routine.

## Settings

| | |
|---|---|
| **Repository** | `russellkt/practice-report-archive` (public — the run needs no credentials to read) |
| **Branch** | `main` |
| **Schedule** | `47 16,20 * * 3,4,5` — 4:47pm and 8:47pm local, Wed/Thu/Fri |
| **Model** | Haiku is enough; this is mechanical |
| **Tools** | Bash, Read, Write, Glob, Grep |

**Twice a day on purpose.** Clubs file through the afternoon and amend, and
west-coast clubs file late. The 4:47 run catches the first wave, the 8:47 run
catches the settled state. When two captures land on the same day the later one
wins, because it is a correction rather than a duplicate.

The odd minutes are deliberate — `:00` and `:30` are when everyone else's jobs
fire.

**Write access is the one thing to verify.** The run has to push, or the
capture dies with the sandbox. Check the first run's output for `PUSH=0`.

## Prompt

```
Capture today's NFL practice report. MECHANICAL job: run the tool, commit what
it produced, push. Do not improvise and do not edit the parser.

nfl.com/injuries carries ONE Practice Status column, overwritten daily. A day
not captured is gone from every source and cannot be backfilled. Committing and
pushing IS the storage -- this sandbox is discarded when you finish.

1. cd into the repo, confirm branch: git branch --show-current
2. python3 tools/nfl_injury_report.py; echo EXIT=$?
   (it archives the raw page to data/raw/ BEFORE parsing, so bytes survive a
   parse failure)
3. python3 tools/practice_progression.py --csv progression/progression.csv; echo EXIT=$?
4. Commit and push everything under data/ and progression/ EVEN IF steps 2 or 3
   failed:
     git add -A data progression
     git -c user.name='practice-report-bot' -c user.email='russellkt@gmail.com' \
         commit -m "Capture <date>: <N> clubs, <M> players"
     git push origin main; echo PUSH=$?
   Nothing changed is fine, say so and stop. If the push is rejected because the
   remote moved: git pull --rebase origin main, then push once more. Never
   force-push.
5. Report in <=3 lines: exit codes, clubs filed and players, which days the
   progression has and which are missing, push result.

If the collector exits non-zero it printed COLLECTION FAILED with a reason.
Report it verbatim and still commit whatever reached data/raw/. Do NOT modify
tools/ to make it pass -- a parser that adapts until the numbers agree has
defeated the check that exists to catch exactly that. A loud failure with the
bytes saved is correct; the parse can be redone later with --from-raw, but only
if the bytes were pushed.

If you cannot push, say so prominently and quote the git error: that means the
capture was lost and a human needs to know today.
```

## Why the prompt forbids self-repair

The collector counts participation strings in the raw HTML and refuses to write
if fewer reached a record. An agent told to "adapt when the page changes" is
being asked to make that count agree — which is the same objective as defeating
the check. It would convert a loud failure into confident wrong data, on a feed
where wrong reads as "he practised fine".

The safe version of self-repair is different: the deterministic parser fails
closed, the failure is the trigger, and a fix is proposed against `data/raw/` as
a regression corpus — every day already captured has to keep parsing identically.
That is why the bytes are archived before the parse, and why the push matters
even on a failed run.

## Running it by hand

```bash
python3 tools/nfl_injury_report.py
python3 tools/practice_progression.py --csv progression/progression.csv
git add -A data progression && git commit -m "Capture $(date +%F)" && git push
```
