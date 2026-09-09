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

  uv run python draft2026/tools/practice_progression.py
  uv run python draft2026/tools/practice_progression.py --league fairhope
  uv run python draft2026/tools/practice_progression.py --csv out.csv
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAP_DIR = ROOT / "data"
OUT_DIR = ROOT / "progression"

PRACTICE_DAYS = ("Wednesday", "Thursday", "Friday")
RANK = {"DNP": 0, "LIMITED": 1, "FULL": 2}


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


def build(snap_dir=SNAP_DIR, week=None, league=None):
    snaps = load_snapshots(snap_dir, week)
    # (player_slug or name) -> {weekday: record}, newest capture per day wins
    by_player = defaultdict(dict)
    meta = {}
    days_seen, clubs_by_day = {}, defaultdict(set)
    for d, fname in snaps:
        day = d.get("weekday")
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
        "snapshots_used": [n for _, n in snaps],
        "days_captured": sorted(days_seen, key=lambda d: PRACTICE_DAYS.index(d)
                                if d in PRACTICE_DAYS else 9),
        "days_missing": [d for d in PRACTICE_DAYS if d not in days_seen],
        "clubs_by_day": {k: sorted(v) for k, v in clubs_by_day.items()},
        "caveat": ("Participation only. FULL means the club filed Full "
                   "Participation -- not that no snap limit exists. A day never "
                   "captured cannot be recovered from any source."),
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
    ap.add_argument("--league", default=None)
    ap.add_argument("--snap-dir", type=Path, default=SNAP_DIR)
    ap.add_argument("--csv", type=Path, default=None,
                    help="also write a flat CSV, the shape a published sheet wants")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        rep = build(args.snap_dir, args.week, args.league)
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
        print(f"  {rep['players']} players | captured {rep['days_captured'] or 'nothing'}"
              f" | MISSING {rep['days_missing'] or 'none'}")
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
