#!/usr/bin/env python3
"""Assemble Wed/Thu/Fri practice participation into the series nobody publishes.

THE SNAPSHOTS ARE RAW MATERIAL; THIS IS THE ARTIFACT. nfl_injury_report.py
captures one day, because the NFL's page carries a single Practice Status
column that is overwritten each day. Neither the NFL nor nflverse keeps the
history: both store one status per player-week. What has value is the SHAPE
across the week, because that is what the promotion rule actually turns on --
DNP, Limited, Full is a player arriving; Full, Limited, DNP is one leaving;
and both collapse to the same single value everywhere else.

WHAT A TREND IS NOT. This grades participation and nothing else. It does not
read "questionable", it does not guess at snap counts, and it does not decide
whether to start anyone. A player can practise in full and be on a pitch
count nobody filed -- espn_injuries.py's verbatim commentary is where that
lives. FULL here means the club filed Full Participation, no more.

DAYS ARE NAMED BY THE CAPTURE'S WEEKDAY, not by position in a list. Captures
can be missed, doubled, or run at odd hours, so a bare third element is not
"Friday". A day nobody captured is reported as MISSING rather than skipped,
because a two-day week and a three-day week with a hole are different claims.

...AND THE WEEKDAY IS TAKEN FROM THE CLOCK, NOT THE DATE. The page carries one
Practice Status column and it is only overwritten once the clubs file, so the
evening capture -- which lands on the following UTC date -- holds the previous
day's report. A capture taken before any club could have filed is attributed
back a day (practice_day), and one that restates the previous day unchanged is
dropped rather than allowed to open a column (drop_stale_restatements). Between
them, a day nobody filed stays MISSING instead of being invented out of the
page that was already there.

  uv run python draft2026/tools/practice_progression.py
  uv run python draft2026/tools/practice_progression.py --league fairhope
  uv run python draft2026/tools/practice_progression.py --csv out.csv
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAP_DIR = ROOT / "data"
OUT_DIR = ROOT / "progression"

PRACTICE_DAYS = ("Wednesday", "Thursday", "Friday")
RANK = {"DNP": 0, "LIMITED": 1, "FULL": 2}

# Clubs practise mid-day and file in the afternoon. 15:00 UTC is 10:00 ET and
# 07:00 PT: before it, no club in the league has filed that day, so a capture
# taken earlier carries the PREVIOUS day's column no matter what date is on it.
FILING_CUTOFF_UTC = 15


class ProgressionError(RuntimeError):
    """Fail closed: an empty progression is not a healthy roster."""


def load_snapshots(snap_dir=SNAP_DIR, week=None):
    """Newest capture per (date, club) wins -- a day run twice is the same day."""
    files = sorted(snap_dir.glob("practice-*.json"))
    if not files:
        raise ProgressionError(
            f"no captures in {snap_dir}. The progression is built from daily "
            f"snapshots and cannot be reconstructed after the fact -- run "
            f"nfl_injury_report.py first, and on the days you care about.")
    snaps = []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ProgressionError(f"{f.name} is not valid JSON: {exc}") from exc
        if week is not None and d.get("week") not in (None, week):
            continue
        snaps.append((d, f.name))
    if not snaps:
        raise ProgressionError(f"no captures matched week {week}")
    return snaps


def practice_date(d):
    """The DATE a capture's filings belong to, which is not always its date.

    THE PAGE LAGS THE CALENDAR. The overnight capture -- cron '47 1 * * 4,5,6',
    20:47 local the previous evening, the workflow's own comment says so -- is
    stamped with the following UTC date, because that is when it ran. Reading
    that stamp as the practice day is what put Thursday's filings in a Friday
    column on 2026-09-11, and what filed ARI's late-arriving Wednesday report
    as Thursday participation the day before, turning five 49ers amending a
    Wednesday entry into ARRIVING.

    So the day is taken from the clock, not from the date: a capture before the
    hour any club could have filed belongs to the day before. The snapshots keep
    their own `weekday` untouched -- they record when we fetched, this records
    what we fetched.

    A FULL DATE, NOT A WEEKDAY NAME. This returned "Friday" until 2026-09-18,
    and a name is not enough to tell one Friday from the one before it. See
    select_week.
    """
    raw = d.get("captured_at")
    try:
        at = datetime.fromisoformat(raw).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None  # a capture with no clock cannot be placed on a date
    if at.hour < FILING_CUTOFF_UTC:
        at -= timedelta(days=1)
    return at.date()


def practice_day(d):
    """The weekday name of practice_date, or the capture's own label if undated."""
    day = practice_date(d)
    return day.strftime("%A") if day else d.get("weekday")


def week_anchor(day):
    """The Wednesday that opens the practice week containing `day`.

    Wednesday is the anchor because it is the first day clubs file. Wed-Sun map
    back to the Wednesday that opened the week; Mon and Tue map FORWARD, to the
    week about to be filed, because a Monday or Tuesday column only ever arrives
    from a capture that fired ahead of the Wednesday filings.
    """
    if day is None:
        return None
    wd = day.weekday()  # Mon=0 .. Sun=6, Wed=2
    return day + timedelta(days=(2 - wd) if wd < 2 else -(wd - 2))


def select_week(snaps, week_of=None):
    """Keep one practice week. Everything else is named, not silently merged.

    WHY THIS EXISTS. Everything downstream is keyed by WEEKDAY -- practice_wed,
    practice_thu, practice_fri -- and until 2026-09-18 the assembler read every
    capture in the archive into those three columns. That was correct for
    exactly as long as the archive held one week.

    With two weeks on disk it silently interleaved them. Measured on the
    published feed 2026-09-18: Wednesday and Thursday came from that week's
    captures, and FRIDAY came from practice-2026-09-12 -- the week before --
    because no Friday capture of the current week existed yet. The report said
    days_captured Wed/Thu/Fri and days_missing [], so it read as a complete
    week. Zay Flowers showed DNP/DNP/FULL, full_participation_final true, trend
    ARRIVING, where the FULL was eight days old and his actual week was DNP/DNP
    with Friday not yet filed.

    That is the exact failure this module was written to refuse -- a day nobody
    captured must be MISSING rather than filled in from the page that happened
    to be lying around. It was refused per-day and not per-week.
    """
    by_week, undated = {}, []
    for d, fname in snaps:
        anchor = week_anchor(practice_date(d))
        if anchor is None:
            undated.append((d, fname))
            continue
        by_week.setdefault(anchor, []).append((d, fname))
    if not by_week:
        raise ProgressionError(
            "no capture carries a usable `captured_at`, so none can be placed "
            "in a practice week. A week assembled from undated captures would "
            "be a guess about which week it describes.")
    if week_of is None:
        anchor = max(by_week)
    else:
        anchor = week_anchor(week_of)
        if anchor not in by_week:
            raise ProgressionError(
                f"no captures for the practice week of {anchor}. Weeks on disk: "
                f"{', '.join(str(k) for k in sorted(by_week))}")
    excluded = [
        {"file": fname, "captured_at": d.get("captured_at"),
         "filed_for": str(practice_date(d)),
         "week_of": str(week_anchor(practice_date(d))),
         "why": "belongs to a different practice week than the one built"}
        for k, v in sorted(by_week.items()) if k != anchor for d, fname in v
    ] + [
        {"file": fname, "captured_at": d.get("captured_at"), "filed_for": None,
         "week_of": None,
         "why": "no usable capture timestamp, so it cannot be placed in a week"}
        for d, fname in undated
    ]
    return by_week[anchor], anchor, excluded


def filed_state(d):
    """What the clubs actually filed, with nothing about when we fetched it."""
    return tuple(sorted(
        (t.get("team"), p.get("player_slug") or p.get("player"),
         p.get("practice"), p.get("game_status"), p.get("injury"))
        for t in d.get("teams", []) for p in t.get("players", [])))


def drop_stale_restatements(snaps):
    """A capture identical to the previous day's is a stale page, not a new day.

    The page carries one Practice Status column, so a run that fires before the
    clubs have filed reads YESTERDAY's column with today's date stamped on it.
    Taken at face value that manufactures a day: the 06:38 UTC run on
    2026-09-11 was identical, player for player, to the 23:25 run the night
    before, and it opened a Friday column out of Thursday's filings -- which
    then graded sixteen players ARRIVING on a day that had not happened yet.

    Thirty-two clubs filing identical reports on consecutive days, across every
    player on the page, has not been observed and would be indistinguishable
    from this failure in any case. So the trade is deliberate: a day we cannot
    tell apart from a stale read is reported MISSING rather than asserted.
    """
    kept, stale = [], []
    for d, fname in sorted(snaps, key=lambda s: s[0].get("captured_at", "")):
        prior = next((k for k in reversed(kept)
                      if practice_date(k[0]) != practice_date(d)), None)
        if prior is not None and filed_state(prior[0]) == filed_state(d):
            stale.append({
                "file": fname,
                "weekday": practice_day(d),
                "captured_at": d.get("captured_at"),
                "restates": prior[1],
                "why": ("identical to the previous day's filing -- the page had "
                        "not been updated when this run fired"),
            })
            continue
        kept.append((d, fname))
    return kept, stale


def classify(days):
    """Name the shape of the week from the days actually captured.

    Deliberately conservative about ARRIVING: it requires ending Full, because
    the promotion rule turns on full participation and nothing weaker. A player
    who goes DNP -> Limited and stops there is LIMITED_STEADY, not arriving.
    """
    seen = [d for d in days if d is not None]
    if not seen:
        return "NO_DATA"
    if len(seen) == 1:
        return {"FULL": "FULL_SINGLE", "LIMITED": "LIMITED_SINGLE",
                "DNP": "DNP_SINGLE"}[seen[0]]
    ranks = [RANK[s] for s in seen]
    if all(r == 2 for r in ranks):
        return "FULL_ALL_WEEK"
    if all(r == 0 for r in ranks):
        return "DNP_ALL_WEEK"
    rising = all(b >= a for a, b in zip(ranks, ranks[1:])) and ranks[-1] > ranks[0]
    falling = all(b <= a for a, b in zip(ranks, ranks[1:])) and ranks[-1] < ranks[0]
    if rising:
        return "ARRIVING" if ranks[-1] == 2 else "IMPROVING"
    if falling:
        return "DECLINING"
    if all(r == 1 for r in ranks):
        return "LIMITED_STEADY"
    return "MIXED"


def build(snap_dir=SNAP_DIR, week=None, league=None, week_of=None):
    # Staleness is checked across EVERY capture on disk before a week is picked,
    # because the run that restates a stale page is usually the first of a new
    # week -- it reads the page the previous Friday left behind. Checking only
    # inside the selected week would be checking after the one comparison that
    # catches it.
    kept, stale = drop_stale_restatements(load_snapshots(snap_dir, week))
    snaps, anchor, off_week = select_week(kept, week_of)
    # (player_slug or name) -> {weekday: record}, newest capture per day wins
    by_player = defaultdict(dict)
    meta = {}
    days_seen, clubs_by_day, reattributed = {}, defaultdict(set), []
    for d, fname in snaps:
        day = practice_day(d)
        if day != d.get("weekday"):
            reattributed.append({"file": fname, "captured_at": d.get("captured_at"),
                                 "stamped": d.get("weekday"), "filed_for": day,
                                 "why": (f"captured before {FILING_CUTOFF_UTC}:00 "
                                         f"UTC, so it carries the previous day's "
                                         f"column")})
        days_seen[day] = max(days_seen.get(day, ""), d.get("captured_at", ""))
        for t in d.get("teams", []):
            clubs_by_day[day].add(t.get("team"))
            for p in t.get("players", []):
                key = p.get("player_slug") or p.get("player")
                meta[key] = {"player": p.get("player"), "team": t.get("team"),
                             "position": p.get("position")}
                prev = by_player[key].get(day)
                if prev is None or d.get("captured_at", "") >= prev["_at"]:
                    by_player[key][day] = {
                        "practice": p.get("practice"),
                        "injury": p.get("injury"),
                        "game_status": p.get("game_status"),
                        "_at": d.get("captured_at", ""),
                    }

    rows = []
    for key, per_day in sorted(by_player.items(), key=lambda kv: meta[kv[0]]["player"]):
        days = [per_day.get(d, {}).get("practice") for d in PRACTICE_DAYS]
        captured = [d for d in PRACTICE_DAYS if d in days_seen]
        # A day we never captured is MISSING; a day captured where this club
        # had not filed is NOT_FILED. Both are absent from `days`, and saying
        # which is the difference between "we do not know" and "no report".
        gaps = [d for d in PRACTICE_DAYS if d not in days_seen]
        latest = next((per_day[d] for d in reversed(PRACTICE_DAYS) if d in per_day), {})
        rows.append({
            "player": meta[key]["player"], "player_slug": key,
            "team": meta[key]["team"], "position": meta[key]["position"],
            "practice_wed": days[0], "practice_thu": days[1], "practice_fri": days[2],
            "trend": classify(days),
            "days_captured": captured, "days_missing": gaps,
            "injury": latest.get("injury"),
            "game_status": latest.get("game_status"),
            "full_participation_final": days[-1] == "FULL" or (
                days[-1] is None and next(
                    (d for d in reversed(days) if d is not None), None) == "FULL"),
        })

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "week": week,
        "week_of": str(anchor),
        "snapshots_used": [n for _, n in snaps],
        "snapshots_excluded_other_weeks": off_week,
        "snapshots_ignored_as_stale": stale,
        "snapshots_reattributed": reattributed,
        "days_captured": sorted(days_seen, key=lambda d: PRACTICE_DAYS.index(d)
                                if d in PRACTICE_DAYS else 9),
        "days_missing": [d for d in PRACTICE_DAYS if d not in days_seen],
        "clubs_by_day": {k: sorted(v) for k, v in clubs_by_day.items()},
        # The newest capture that fed each column. A consumer that timestamps
        # these filings must use these and not the order of snapshots_used:
        # that is how the downstream reader came to date this week's filings
        # to last week (fantasy26-eu8).
        "day_captured_at": dict(sorted(days_seen.items(),
                                       key=lambda kv: PRACTICE_DAYS.index(kv[0])
                                       if kv[0] in PRACTICE_DAYS else 9)),
        "caveat": ("Participation only. FULL means the club filed Full "
                   "Participation -- not that no snap limit exists. A day never "
                   "captured cannot be recovered from any source." + (
                       f" {len(stale)} capture(s) restated the previous day's "
                       f"filing unchanged and were ignored rather than read as "
                       f"a new day; see snapshots_ignored_as_stale."
                       if stale else "") + (
                       f" Built from the practice week beginning {anchor}; "
                       f"{len(off_week)} capture(s) from other weeks were "
                       f"excluded rather than read into these columns, so a "
                       f"day listed in days_missing is missing THIS week and "
                       f"is not filled from the week before."
                       if off_week else "")),
        "players": len(rows),
        "progression": rows,
    }
    if league:
        import subprocess
        proc = subprocess.run(["uv", "run", "python", "cli.py", "lineup", "--json",
                               "--league", league], cwd=ROOT.parent,
                              capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise ProgressionError(f"cli.py lineup exited {proc.returncode}")
        lu = (json.loads(proc.stdout).get("set_lineup") or {})
        names = {r["player"].lower(): r for r in rows}
        ours = []
        for g in ("starters", "bench", "ir"):
            for p in lu.get(g) or []:
                hit = names.get((p.get("name") or "").lower())
                if hit:
                    ours.append({**hit, "roster_slot": p.get("slot"), "roster_group": g})
        report["league"] = league
        report["ours"] = ours
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--week-of", type=datetime.fromisoformat, default=None,
                    metavar="YYYY-MM-DD",
                    help="build the practice week containing this date "
                         "(default: the newest week on disk)")
    ap.add_argument("--league", default=None)
    ap.add_argument("--snap-dir", type=Path, default=SNAP_DIR)
    ap.add_argument("--csv", type=Path, default=None,
                    help="also write a flat CSV, the shape a published sheet wants")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        week_of = args.week_of.date() if args.week_of else None
        rep = build(args.snap_dir, args.week, args.league, week_of)
    except (ProgressionError, OSError) as exc:
        print(f"PROGRESSION FAILED: {exc}", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = args.out or (OUT_DIR / f"progression-{datetime.now(timezone.utc):%Y-%m-%d}.json")
    out.write_text(json.dumps(rep, indent=2), encoding="utf-8")

    if args.csv:
        cols = ["player", "team", "position", "practice_wed", "practice_thu",
                "practice_fri", "trend", "injury", "game_status",
                "full_participation_final"]
        with args.csv.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rep["progression"])

    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print(f"wrote {out}")
        print(f"  week of {rep['week_of']} | {rep['players']} players | "
              f"captured {rep['days_captured'] or 'nothing'}"
              f" | MISSING {rep['days_missing'] or 'none'}")
        if rep["snapshots_excluded_other_weeks"]:
            print(f"  OTHER WEEKS: {len(rep['snapshots_excluded_other_weeks'])} "
                  f"capture(s) set aside, not merged into these columns")
        for rc in rep["snapshots_reattributed"]:
            print(f"  MOVED   {rc['file']}: stamped {rc['stamped']}, "
                  f"filed for {rc['filed_for']}")
        for sc in rep["snapshots_ignored_as_stale"]:
            print(f"  IGNORED {sc['file']} ({sc['weekday']}): restates "
                  f"{sc['restates']} unchanged")
        shown = rep.get("ours") or rep["progression"]
        label = "ours" if rep.get("ours") else "all"
        for r in shown[:15]:
            days = "/".join((d or "--")[:4] for d in
                            (r["practice_wed"], r["practice_thu"], r["practice_fri"]))
            print(f"    [{label}] {r['player']:<22} {r['team'] or '?':<4} "
                  f"{days:<16} {r['trend']:<15} {r['injury'] or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
