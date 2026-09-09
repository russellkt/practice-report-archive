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
