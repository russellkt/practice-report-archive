#!/usr/bin/env python3
"""Snapshot the NFL's own daily practice report, because nobody publishes it twice.

WHY THIS EXISTS. The practice report is an NFL-mandated filing -- teams report
Wed/Thu/Fri participation plus a final game status -- and nfl.com/injuries is
that filing, served as plain server-rendered HTML with no auth, no token and
no JavaScript. Verified 2026-09-09: the page's 9 DNP / 14 Limited / 6 Full is
byte-for-byte the same set nflverse publishes, because nflverse scrapes this
page. Going direct removes their publication lag, which matters because their
injurybot's cron is COMMENTED OUT and the repo shows zero workflow runs -- the
data is pushed by something self-hosted on a schedule nobody documents.

THE PART THAT CANNOT BE BACKFILLED, WHICH IS THE ONLY REASON THIS IS URGENT.
The page carries ONE `Practice Status` column. It holds Wednesday's
participation today and Thursday's tomorrow -- overwritten, never appended.
The archived week URLs (/injuries/league/2025/REG10) keep the same five
columns, so they preserve the FINAL state and not the three days. So the
progression that the bridge rule actually turns on -- Limited, Limited, Full
is a green light; Full, Limited, DNP is the opposite -- is published by nobody
as a retrievable series. FantasyPros sells it as practice_1/2/3 behind a
ten-row cap. Everyone else, nflverse included, stores one row per player-week.

Capture it and it is yours. Miss a day and that day is gone from every source
on earth. That is why this writes a dated snapshot per run rather than a
current-state file.

  uv run python draft2026/tools/nfl_injury_report.py
  uv run python draft2026/tools/nfl_injury_report.py --week 10 --year 2025
  uv run python draft2026/tools/nfl_injury_report.py --league fairhope --json
"""
import argparse
import gzip
import html as htmllib
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT
OUT_DIR = ROOT / "data"
INDEX = OUT_DIR / "index.jsonl"
# THE BYTES ARE THE IRREPLACEABLE PART. A broken parser can be fixed tomorrow
# and re-run over these; a fetch that did not happen is gone from every source
# on earth. So the raw page is archived BEFORE parsing, and archived even when
# the parse then fails -- which is precisely the run whose bytes you want.
RAW_DIR = OUT_DIR / "raw"

LIVE_URL = "https://www.nfl.com/injuries/"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36")

# The matchup strips prove the page RENDERED, which is all this gate is for.
# Not 32: a bye week takes clubs off the schedule and therefore off the page --
# week 10 of 2025 shows 28, and an earlier version of this gate rejected that
# perfectly good archive page as a failed render. Six clubs on bye is the most
# the schedule allows, so 26 is the floor; 24 leaves margin without coming
# close to a blank page.
MIN_CLUBS = 24

_SECTION = re.compile(r'<div class="d3-o-section-sub-title"><span>(.*?)</span></div>')
_TABLE = re.compile(r'<table class="d3-o-table.*?</table>', re.S)
_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_SLUG = re.compile(r'href="/players/([a-z0-9\-]+)/"')
_ABBR = re.compile(r'nfl-c-matchup-strip__team-abbreviation">\s*([A-Z]{2,3})\s*<')
# Counted in the raw HTML and compared against what got parsed. A parser that
# silently drops rows is the failure this collector cannot survive, because a
# short report reads as a healthy league rather than as an error.
_STATUS_TEXT = re.compile(r"(Did Not Participate In Practice|"
                          r"Limited Participation in Practice|"
                          r"Full Participation in Practice)")

GRADE = {"Did Not Participate In Practice": "DNP",
         "Limited Participation in Practice": "LIMITED",
         "Full Participation in Practice": "FULL"}

NICK_TO_ABBR = {
    "Cardinals": "ARI", "Falcons": "ATL", "Ravens": "BAL", "Bills": "BUF",
    "Panthers": "CAR", "Bears": "CHI", "Bengals": "CIN", "Browns": "CLE",
    "Cowboys": "DAL", "Broncos": "DEN", "Lions": "DET", "Packers": "GB",
    "Texans": "HOU", "Colts": "IND", "Jaguars": "JAX", "Chiefs": "KC",
    "Raiders": "LV", "Chargers": "LAC", "Rams": "LAR", "Dolphins": "MIA",
    "Vikings": "MIN", "Patriots": "NE", "Saints": "NO", "Giants": "NYG",
    "Jets": "NYJ", "Eagles": "PHI", "Steelers": "PIT", "49ers": "SF",
    "Seahawks": "SEA", "Buccaneers": "TB", "Titans": "TEN", "Commanders": "WAS",
}


class InjuryReportError(RuntimeError):
    """Fail closed: a thin practice report reads as a healthy league."""


def norm(s):
    s = (s or "").lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    s = re.sub(r"[^a-z ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _text(fragment):
    return re.sub(r"\s+", " ",
                  htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise InjuryReportError(f"{url} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise InjuryReportError(f"{url} unreachable: {exc.reason}") from exc


def parse(page):
    """Each club that has filed gets a section title then a table. Clubs that
    have not filed get the title and no table, which is a real state -- on a
    Wednesday morning most of the league has not reported yet."""
    marks = list(_SECTION.finditer(page))
    teams = []
    for idx, mark in enumerate(marks):
        end = marks[idx + 1].start() if idx + 1 < len(marks) else len(page)
        block = page[mark.start():end]
        nick = _text(mark.group(1))
        table = _TABLE.search(block)
        if not table:
            continue
        players = []
        for row in _ROW.findall(table.group(0)):
            cells = _CELL.findall(row)
            if len(cells) < 5:
                continue
            status = _text(cells[3])
            slug = _SLUG.search(cells[0])
            players.append({
                "player": _text(cells[0]),
                "player_slug": slug.group(1) if slug else None,
                "position": _text(cells[1]) or None,
                "injury": _text(cells[2]) or None,
                "practice_status": status or None,
                "practice": GRADE.get(status),
                "game_status": _text(cells[4]) or None,
            })
        if players:
            teams.append({"team": NICK_TO_ABBR.get(nick), "team_name": nick,
                          "players": players})
    return teams


def assert_plausible(teams, page, source):
    """Two gates, and the second is the one that matters.

    The club count proves the PAGE rendered. The status count proves the
    PARSER kept up with it: if the raw HTML holds 29 participation strings and
    only 12 reached a record, seventeen players silently vanished, and a
    practice report that loses players reads as good news about them.
    """
    clubs = len(set(_ABBR.findall(page)))
    if clubs < MIN_CLUBS:
        raise InjuryReportError(
            f"{source} rendered only {clubs} clubs, below the {MIN_CLUBS} floor; "
            f"the page did not render, so an empty report here means nothing.")

    in_html = len(_STATUS_TEXT.findall(page))
    parsed = sum(1 for t in teams for p in t["players"] if p["practice"])
    if parsed != in_html:
        raise InjuryReportError(
            f"{source} contains {in_html} practice statuses but only {parsed} "
            f"reached a record. The table markup has moved and players are "
            f"being dropped silently.")
    unmapped = sorted({t["team_name"] for t in teams if not t["team"]})
    if unmapped:
        raise InjuryReportError(f"unmapped club nicknames in {source}: {unmapped}")
    return teams


def load_roster(league):
    cmd = ["uv", "run", "python", "cli.py", "lineup", "--json", "--league", league]
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise InjuryReportError(
            f"cli.py lineup exited {proc.returncode} for league {league}; cannot "
            f"say which of these players are ours. {(proc.stderr or '').strip()[-300:]}")
    payload = json.loads(proc.stdout)
    lineup = payload.get("set_lineup") or {}
    roster = [{"name": p.get("name"), "position": p.get("position"),
               "team": p.get("pro_team"), "slot": p.get("slot"), "group": g}
              for g in ("starters", "bench", "ir") for p in (lineup.get(g) or [])]
    if not roster:
        raise InjuryReportError("cli.py lineup returned a roster of zero players")
    return payload.get("team_name"), roster


def archive_raw(page, now, week=None):
    """Store the page gzipped before anything can reject it. ~330KB raw, well
    under 40KB gzipped, so a full season of daily captures is a few megabytes."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"week{week}-" if week else ""
    path = RAW_DIR / f"nfl-injuries-{tag}{now:%Y-%m-%d-%H%M}.html.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(page)
    return path


def build(week=None, year=None, league=None, timeout=30, raw=True):
    if week is not None:
        yr = year or datetime.now().year
        url = f"https://www.nfl.com/injuries/league/{yr}/REG{week}"
    else:
        url = LIVE_URL
    page = fetch(url, timeout)
    now = datetime.now(timezone.utc)
    raw_path = archive_raw(page, now, week) if raw else None
    teams = assert_plausible(parse(page), page, url)

    filed = [t["team"] for t in teams]
    report = {
        "captured_at": now.isoformat(timespec="seconds"),
        "capture_date": now.strftime("%Y-%m-%d"),
        "weekday": now.strftime("%A"),
        "source": url,
        "raw": raw_path.name if raw_path else None,
        "week": week,
        "note": ("A SNAPSHOT, not a current-state file. The page carries one "
                 "Practice Status column which is overwritten each day, so this "
                 "day's participation exists only in captures like this one."),
        "clubs_filed": len(filed),
        "clubs_filed_list": sorted(filed),
        "players": sum(len(t["players"]) for t in teams),
        "teams": teams,
    }
    if league:
        team_name, roster = load_roster(league)
        index = {}
        for t in teams:
            for p in t["players"]:
                index.setdefault(norm(p["player"]), []).append({**p, "team": t["team"]})
        ours, clear = [], []
        for slot in roster:
            hits = index.get(norm(slot["name"]))
            if hits:
                ours.append({**hits[0], "roster_slot": slot["slot"],
                             "roster_group": slot["group"]})
            else:
                clear.append(slot)
        report["league"] = league
        report["team_name"] = team_name
        report["ours"] = ours
        report["not_on_report"] = [
            {**c, "why": ("not on any filed report -- which means healthy ONLY if "
                          "his club has filed; check clubs_filed_list")}
            for c in clear]
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week", type=int, default=None,
                    help="archived week (final status only, not the day's report)")
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("--league", default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--no-raw", action="store_true",
                    help="skip the gzipped page archive (you almost never want this)")
    ap.add_argument("--from-raw", type=Path, default=None,
                    help="re-parse an archived page instead of fetching")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.from_raw:
            import gzip as _gz
            opener = _gz.open if str(args.from_raw).endswith(".gz") else open
            with opener(args.from_raw, "rt", encoding="utf-8") as fh:
                page = fh.read()
            teams = assert_plausible(parse(page), page, str(args.from_raw))
            now = datetime.now(timezone.utc)
            filed = [t["team"] for t in teams]
            report = {"captured_at": now.isoformat(timespec="seconds"),
                      "capture_date": now.strftime("%Y-%m-%d"),
                      "weekday": now.strftime("%A"), "source": str(args.from_raw),
                      "raw": args.from_raw.name, "week": args.week,
                      "note": "RE-PARSED from an archived page.",
                      "clubs_filed": len(filed), "clubs_filed_list": sorted(filed),
                      "players": sum(len(t["players"]) for t in teams),
                      "teams": teams}
        else:
            report = build(args.week, args.year, args.league, raw=not args.no_raw)
    except (InjuryReportError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"COLLECTION FAILED: {exc}", file=sys.stderr)
        return 2

    stamp = report["capture_date"]
    tag = f"week{args.week}-" if args.week else ""
    out = args.out or (OUT_DIR / f"practice-{tag}{stamp}-"
                       f"{datetime.now(timezone.utc):%H%M}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    with INDEX.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"captured_at": report["captured_at"],
                             "file": out.name, "clubs_filed": report["clubs_filed"],
                             "players": report["players"]}) + "\n")

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"wrote {out}")
        print(f"  {report['clubs_filed']}/32 clubs filed, {report['players']} players")
        for p in report.get("ours", []):
            print(f"    {p['player']:<22} {p['position'] or '?':<3} {p['team'] or '?':<4} "
                  f"{p['roster_slot']:<4} {p['practice'] or '?':<8} "
                  f"{p['injury'] or ''} {('[' + p['game_status'] + ']') if p['game_status'] else ''}")
        if report.get("ours") == []:
            print("    no rostered player on any filed report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
