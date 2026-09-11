"""Tests for assembling the Wed/Thu/Fri practice series.

This is the artifact the daily captures exist to produce, and its failure mode
is quiet: a week with a missed capture looks exactly like a week where nobody
practised, unless the tool insists on the difference. So most of what is
tested here is the tool refusing to overclaim.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "practice_progression", ROOT / "tools" / "practice_progression.py")
pp = importlib.util.module_from_spec(_spec)
sys.modules["practice_progression"] = pp
_spec.loader.exec_module(pp)


def _snap(tmp, day, date, players, club="NYG"):
    d = {"captured_at": f"{date}T18:00:00+00:00", "capture_date": date,
         "weekday": day, "source": "test", "week": None,
         "clubs_filed": 1, "clubs_filed_list": [club],
         "players": len(players),
         "teams": [{"team": club, "team_name": club, "players": players}]}
    (tmp / f"practice-{date}-1800.json").write_text(json.dumps(d))


def _p(name, practice, slug=None, injury="Knee", game=""):
    return {"player": name, "player_slug": slug or name.lower().replace(" ", "-"),
            "position": "WR", "injury": injury,
            "practice_status": practice, "practice": practice, "game_status": game}


# --- the shape of a week ----------------------------------------------------

@pytest.mark.parametrize("days,expected", [
    (["DNP", "LIMITED", "FULL"], "ARRIVING"),
    (["DNP", "DNP", "LIMITED"], "IMPROVING"),
    (["FULL", "LIMITED", "DNP"], "DECLINING"),
    (["FULL", "FULL", "FULL"], "FULL_ALL_WEEK"),
    (["DNP", "DNP", "DNP"], "DNP_ALL_WEEK"),
    (["LIMITED", "LIMITED", "LIMITED"], "LIMITED_STEADY"),
    (["LIMITED", "FULL", "LIMITED"], "MIXED"),
    (["FULL", None, None], "FULL_SINGLE"),
    ([None, None, None], "NO_DATA"),
])
def test_trend_names_the_shape(days, expected):
    assert pp.classify(days) == expected


def test_arriving_requires_ending_full_not_merely_rising():
    """The promotion rule turns on full participation and nothing weaker, so a
    player who climbs to Limited and stops has not arrived."""
    assert pp.classify(["DNP", "LIMITED", "LIMITED"]) == "IMPROVING"
    assert pp.classify(["DNP", "LIMITED", "FULL"]) == "ARRIVING"


def test_gaps_do_not_shift_days_left():
    """A player captured Wed and Fri only is Wed->Fri, not Wed->Thu. Naming
    days by weekday rather than by list position is what prevents that."""
    assert pp.classify(["DNP", None, "FULL"]) == "ARRIVING"


# --- refusing to overclaim --------------------------------------------------

def test_a_missed_day_is_reported_as_missing(tmp_path):
    _snap(tmp_path, "Wednesday", "2026-09-09", [_p("Malik Nabers", "LIMITED")])
    _snap(tmp_path, "Friday", "2026-09-11", [_p("Malik Nabers", "FULL")])
    rep = pp.build(tmp_path)
    assert rep["days_captured"] == ["Wednesday", "Friday"]
    assert rep["days_missing"] == ["Thursday"]
    row = rep["progression"][0]
    assert (row["practice_wed"], row["practice_thu"], row["practice_fri"]) == \
        ("LIMITED", None, "FULL")
    assert row["days_missing"] == ["Thursday"]


def test_no_captures_at_all_is_an_error_not_an_empty_week(tmp_path):
    with pytest.raises(pp.ProgressionError, match="cannot be reconstructed"):
        pp.build(tmp_path)


def test_a_corrupt_capture_is_named_rather_than_skipped(tmp_path):
    _snap(tmp_path, "Wednesday", "2026-09-09", [_p("Malik Nabers", "FULL")])
    (tmp_path / "practice-2026-09-10-1800.json").write_text("{not json")
    with pytest.raises(pp.ProgressionError, match="practice-2026-09-10-1800.json"):
        pp.build(tmp_path)


def test_the_same_day_captured_twice_keeps_the_later_run(tmp_path):
    """Clubs amend filings during the day, so a second run is a correction and
    not a duplicate player."""
    _snap(tmp_path, "Wednesday", "2026-09-09", [_p("Malik Nabers", "DNP")])
    d = json.loads((tmp_path / "practice-2026-09-09-1800.json").read_text())
    d["captured_at"] = "2026-09-09T23:00:00+00:00"
    d["teams"][0]["players"] = [_p("Malik Nabers", "FULL")]
    (tmp_path / "practice-2026-09-09-2300.json").write_text(json.dumps(d))
    rep = pp.build(tmp_path)
    assert rep["players"] == 1
    assert rep["progression"][0]["practice_wed"] == "FULL"


def test_full_participation_final_reads_the_last_day_actually_captured(tmp_path):
    _snap(tmp_path, "Wednesday", "2026-09-09", [_p("Malik Nabers", "FULL")])
    rep = pp.build(tmp_path)
    assert rep["progression"][0]["full_participation_final"] is True
    _snap(tmp_path, "Thursday", "2026-09-10", [_p("Malik Nabers", "DNP")])
    rep = pp.build(tmp_path)
    assert rep["progression"][0]["full_participation_final"] is False


def test_players_are_tracked_by_slug_so_a_renamed_display_name_does_not_split(tmp_path):
    _snap(tmp_path, "Wednesday", "2026-09-09",
          [_p("Malik Nabers", "DNP", slug="malik-nabers")])
    _snap(tmp_path, "Thursday", "2026-09-10",
          [_p("Malik  Nabers Jr.", "FULL", slug="malik-nabers")])
    rep = pp.build(tmp_path)
    assert rep["players"] == 1
    assert rep["progression"][0]["trend"] == "ARRIVING"


def test_the_caveat_travels_with_the_data(tmp_path):
    """FULL means the club filed Full Participation, not that no snap limit
    exists. Anything reading this file needs that in front of it."""
    _snap(tmp_path, "Wednesday", "2026-09-09", [_p("Malik Nabers", "FULL")])
    rep = pp.build(tmp_path)
    assert "not that no snap limit exists" in rep["caveat"]
    assert rep["snapshots_used"] == ["practice-2026-09-09-1800.json"]


# --- a capture is not evidence of a day -------------------------------------

def _snap_at(tmp, date, at, players, club="NYG", day=None):
    """A capture at a named UTC hour. `day` defaults to the stamp the collector
    would write, which is the calendar weekday of the capture -- the thing the
    assembler must not take at face value."""
    from datetime import datetime
    stamped = day or datetime.fromisoformat(date).strftime("%A")
    d = {"captured_at": f"{date}T{at}:00+00:00", "capture_date": date,
         "weekday": stamped, "source": "test", "week": None,
         "clubs_filed": 1, "clubs_filed_list": [club],
         "players": len(players),
         "teams": [{"team": club, "team_name": club, "players": players}]}
    (tmp / f"practice-{date}-{at.replace(':', '')}.json").write_text(json.dumps(d))


def test_the_overnight_run_is_filed_under_the_day_it_actually_read(tmp_path):
    """cron '47 1 * * 4,5,6' fires at 20:47 local the evening before, so its
    capture carries that evening's column under the next morning's date. Read
    literally it invents a day: the real 2026-09-11 06:38 run opened a Friday
    column out of Thursday's filings and graded players ARRIVING on it."""
    _snap_at(tmp_path, "2026-09-09", "21:47", [_p("Malik Nabers", "DNP")])
    _snap_at(tmp_path, "2026-09-10", "23:25", [_p("Malik Nabers", "FULL")])
    _snap_at(tmp_path, "2026-09-11", "06:38", [_p("Malik Nabers", "FULL")])
    rep = pp.build(tmp_path)

    assert rep["days_captured"] == ["Wednesday", "Thursday"]
    assert rep["days_missing"] == ["Friday"]
    row = rep["progression"][0]
    assert row["practice_fri"] is None
    assert row["trend"] == "ARRIVING"  # Wed->Thu only, not a three-day week
    assert row["days_missing"] == ["Friday"]


def test_a_late_filing_seen_overnight_lands_on_the_day_it_was_filed_for(tmp_path):
    """The west-coast clubs file after the evening capture, so the overnight run
    is where their report first appears. It is still THAT day's report: on
    2026-09-10 the 06:39 run carried ARI's Wednesday filing, and reading it as
    Thursday turned a Wednesday amendment into a day of improvement."""
    _snap_at(tmp_path, "2026-09-09", "21:47",
             [_p("Dre Greenlaw", "LIMITED"), _p("Ricky Pearsall", "FULL")])
    _snap_at(tmp_path, "2026-09-10", "06:39",
             [_p("Dre Greenlaw", "FULL"), _p("Ricky Pearsall", "FULL")])
    _snap_at(tmp_path, "2026-09-10", "23:25",
             [_p("Dre Greenlaw", "FULL"), _p("Ricky Pearsall", "LIMITED")])
    rep = pp.build(tmp_path)

    row = next(r for r in rep["progression"] if r["player"] == "Dre Greenlaw")
    assert (row["practice_wed"], row["practice_thu"]) == ("FULL", "FULL")
    assert row["trend"] == "FULL_ALL_WEEK"  # not ARRIVING off a Wednesday edit


def test_a_reattributed_capture_is_named_rather_than_quietly_moved(tmp_path):
    _snap_at(tmp_path, "2026-09-10", "23:25", [_p("Malik Nabers", "LIMITED")])
    _snap_at(tmp_path, "2026-09-11", "06:38", [_p("Malik Nabers", "FULL")])
    rep = pp.build(tmp_path)

    moved = rep["snapshots_reattributed"]
    assert len(moved) == 1
    assert moved[0]["file"] == "practice-2026-09-11-0638.json"
    assert (moved[0]["stamped"], moved[0]["filed_for"]) == ("Friday", "Thursday")
    assert rep["progression"][0]["practice_thu"] == "FULL"  # the later run wins


def test_an_afternoon_capture_keeps_its_own_day(tmp_path):
    """The 21:47 UTC run is 16:47 local, after the clubs have filed. Nothing
    about it is ambiguous and it must not be moved."""
    _snap_at(tmp_path, "2026-09-11", "21:47", [_p("Malik Nabers", "FULL")])
    rep = pp.build(tmp_path)
    assert rep["snapshots_reattributed"] == []
    assert rep["days_captured"] == ["Friday"]


def test_a_page_that_never_changed_does_not_open_a_day(tmp_path):
    """Belt and braces for the clock rule: an afternoon run can still read a
    page the clubs have not touched, and an identical page is not a filing."""
    _snap_at(tmp_path, "2026-09-10", "21:47", [_p("Malik Nabers", "LIMITED")])
    _snap_at(tmp_path, "2026-09-11", "21:47", [_p("Malik Nabers", "LIMITED")])
    rep = pp.build(tmp_path)

    assert rep["days_captured"] == ["Thursday"]
    assert rep["days_missing"] == ["Wednesday", "Friday"]
    ignored = rep["snapshots_ignored_as_stale"]
    assert [s["file"] for s in ignored] == ["practice-2026-09-11-2147.json"]
    assert ignored[0]["restates"] == "practice-2026-09-10-2147.json"
    assert "snapshots_ignored_as_stale" in rep["caveat"]


def test_a_changed_game_status_alone_is_a_real_filing(tmp_path):
    """Friday's news is often the designation, not the participation: the same
    practice status with an Out beside it is new information."""
    _snap_at(tmp_path, "2026-09-10", "21:47",
             [_p("TreVeyon Henderson", "DNP", game="")])
    _snap_at(tmp_path, "2026-09-11", "21:47",
             [_p("TreVeyon Henderson", "DNP", game="Out")])
    rep = pp.build(tmp_path)

    assert rep["snapshots_ignored_as_stale"] == []
    assert rep["days_captured"] == ["Thursday", "Friday"]
    assert rep["progression"][0]["game_status"] == "Out"


def test_the_first_capture_of_a_week_is_never_stale(tmp_path):
    """There is no previous day to restate, so nothing is dropped."""
    _snap_at(tmp_path, "2026-09-09", "21:47", [_p("Malik Nabers", "FULL")])
    rep = pp.build(tmp_path)
    assert rep["snapshots_ignored_as_stale"] == []
    assert rep["days_captured"] == ["Wednesday"]
