"""Tests for the NFL practice-report snapshotter.

WHAT THIS COLLECTOR CANNOT BE ALLOWED TO DO is report fewer hurt players than
the page actually lists. The whole point is a daily capture of a column that
gets overwritten tomorrow, so a silent drop is not a bug you notice later --
the evidence is gone. Every gate here exists for that.

The three real states that look alike and must not be conflated:
  a club with a section title and NO table          -- has not filed yet
  a club with a table and no rostered player in it  -- filed, ours are healthy
  a page that did not render                        -- we know nothing
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "nfl_injury_report", ROOT / "tools" / "nfl_injury_report.py")
nir = importlib.util.module_from_spec(_spec)
sys.modules["nfl_injury_report"] = nir
_spec.loader.exec_module(nir)


def _strip(*abbrs):
    return "".join(f'<span class="nfl-c-matchup-strip__team-abbreviation"> {a} </span>'
                   for a in abbrs)


ALL_CLUBS = _strip(*sorted(nir.NICK_TO_ABBR.values()))


def _row(name, slug, pos, injury, practice, game):
    return (f'<tr><td scope="row" tabindex="0"><a href="/players/{slug}/" '
            f'class="nfl-o-cta--link"> {name} </a></td><td>{pos}</td>'
            f'<td>{injury}</td><td>{practice}</td><td>{game}</td></tr>')


def _team(nick, rows):
    table = ('<div class="d3-o-table--horizontal-scroll">'
             '<table class="d3-o-table d3-o-table--detailed"><thead><tr>'
             '<th>Player</th><th>Position</th><th>Injuries</th>'
             '<th>Practice Status</th><th>Game Status</th></tr></thead><tbody>'
             + "".join(rows) + "</tbody></table></div>") if rows else ""
    return f'<div class="d3-o-section-sub-title"><span>{nick}</span></div>' + table


DNP = "Did Not Participate In Practice"
LIM = "Limited Participation in Practice"
FULL = "Full Participation in Practice"


def _page(teams=None, clubs=ALL_CLUBS):
    body = teams if teams is not None else (
        _team("Patriots", [
            _row("TreVeyon Henderson", "treveyon-henderson", "RB", "Ankle", DNP, "Out"),
            _row("Christian Barmore", "christian-barmore", "DT", "", FULL, ""),
        ])
        + _team("Seahawks", [])          # section title, no table: has not filed
        + _team("Giants", [
            _row("Malik Nabers", "malik-nabers", "WR", "Knee", LIM, "Questionable"),
        ])
    )
    return "<html><body>" + clubs + body + "</body></html>"


# --- the happy path ---------------------------------------------------------

def test_parses_each_filed_club_into_graded_records():
    teams = nir.parse(_page())
    assert [t["team"] for t in teams] == ["NE", "NYG"]
    ne = teams[0]["players"]
    assert ne[0] == {"player": "TreVeyon Henderson", "player_slug": "treveyon-henderson",
                     "position": "RB", "injury": "Ankle",
                     "practice_status": DNP, "practice": "DNP", "game_status": "Out"}
    assert ne[1]["practice"] == "FULL" and ne[1]["injury"] is None


def test_a_club_that_has_not_filed_is_absent_not_empty():
    """Seattle has a section title and no table. Emitting it with zero players
    would say "the Seahawks reported nobody hurt", which is a different and
    much more useful claim than "the Seahawks have not reported"."""
    teams = nir.parse(_page())
    assert "SEA" not in [t["team"] for t in teams]


def test_a_row_with_a_game_status_but_no_practice_cell_is_kept():
    """Real shape: two players in week 10 of 2025 were listed Out with an empty
    practice cell. Dropping them would lose an Out."""
    page = _page(_team("Bears", [_row("Jahdae Walker", "jahdae-walker", "WR",
                                      "Concussion", "", "Out")]))
    p = nir.parse(page)[0]["players"][0]
    assert p["practice"] is None and p["practice_status"] is None
    assert p["game_status"] == "Out" and p["injury"] == "Concussion"


# --- the gate that matters --------------------------------------------------

def test_a_parser_that_drops_players_is_refused():
    """The failure this collector cannot survive. If the HTML holds statuses
    that no record captured, players vanished -- and a practice report that
    loses players reads as good news about them."""
    page = _page()
    orphan = f"<p>{LIM}</p><p>{DNP}</p>"     # statuses outside any parsed table
    with pytest.raises(nir.InjuryReportError, match="reached a record"):
        nir.assert_plausible(nir.parse(page + orphan), page + orphan, "test")


def test_a_page_that_did_not_render_is_refused():
    page = _page(clubs=_strip("NE", "SEA"))
    with pytest.raises(nir.InjuryReportError, match="below the 24 floor"):
        nir.assert_plausible(nir.parse(page), page, "test")


def test_a_bye_week_page_is_not_mistaken_for_a_failed_render():
    """Week 10 of 2025 renders 28 clubs because four were on bye. An earlier
    gate demanded 32 and rejected a perfectly good archive page."""
    clubs = _strip(*sorted(nir.NICK_TO_ABBR.values())[:28])
    page = _page(clubs=clubs)
    assert nir.assert_plausible(nir.parse(page), page, "test")


def test_an_unknown_club_nickname_is_refused_rather_than_filed_as_none():
    page = _page(_team("Sharks", [_row("A B", "a-b", "RB", "Knee", DNP, "Out")]))
    with pytest.raises(nir.InjuryReportError, match="unmapped club"):
        nir.assert_plausible(nir.parse(page), page, "test")


def test_a_league_wide_quiet_day_passes_because_it_is_real():
    """Monday: the page renders, nobody has filed. That is not an error."""
    page = _page(teams="")
    assert nir.assert_plausible(nir.parse(page), page, "test") == []


# --- the roster join --------------------------------------------------------

def test_not_on_report_says_what_it_does_not_know(monkeypatch):
    """Absence only means healthy if the player's club has filed. The record
    has to carry that caveat or a Wednesday capture reads as an all-clear."""
    monkeypatch.setattr(nir, "fetch", lambda url, timeout=30: _page())
    monkeypatch.setattr(nir, "load_roster", lambda league: ("Mid Rustlers", [
        {"name": "Malik Nabers", "position": "WR", "team": "NYG", "slot": "WR", "group": "starters"},
        {"name": "Jonathan Taylor", "position": "RB", "team": "IND", "slot": "RB", "group": "starters"},
    ]))
    rep = nir.build(league="fairhope", raw=False)
    assert [p["player"] for p in rep["ours"]] == ["Malik Nabers"]
    assert rep["ours"][0]["practice"] == "LIMITED"
    assert [p["name"] for p in rep["not_on_report"]] == ["Jonathan Taylor"]
    assert "clubs_filed_list" in rep["not_on_report"][0]["why"]
    assert rep["clubs_filed_list"] == ["NE", "NYG"]


def test_the_snapshot_records_the_day_it_captured(monkeypatch):
    """A capture that does not say which day it is worthless: the whole value
    is assembling Wed/Thu/Fri from separate runs."""
    monkeypatch.setattr(nir, "fetch", lambda url, timeout=30: _page())
    rep = nir.build(raw=False)
    assert rep["capture_date"] and rep["weekday"]
    assert rep["source"] == nir.LIVE_URL
    assert "overwritten each day" in rep["note"]


def test_an_archived_week_is_addressed_by_url(monkeypatch):
    seen = {}
    def fake(url, timeout=30):
        seen["url"] = url
        return _page()
    monkeypatch.setattr(nir, "fetch", fake)
    nir.build(week=10, year=2025, raw=False)
    assert seen["url"] == "https://www.nfl.com/injuries/league/2025/REG10"


def test_the_raw_page_is_archived_where_it_is_told_and_nowhere_else(tmp_path, monkeypatch):
    """The archive is the repository's data. A test must never write into it.

    Three tests here called build() with archiving on and left synthetic pages
    in data/raw named exactly like real captures -- including
    nfl-injuries-2026-09-09-1900.html.gz, which shares its name with a real
    29-player capture. A 3,366-char fixture standing in for a 330,000-char page
    defeats the one thing the raws are for: --from-raw re-parsing every stored
    day as a regression case.
    """
    monkeypatch.setattr(nir, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(nir, "fetch", lambda url, timeout=30: _page())

    rep = nir.build()

    written = list((tmp_path / "raw").glob("*.html.gz"))
    assert len(written) == 1
    assert written[0].name.startswith("nfl-injuries-")
    assert rep["clubs_filed"] == 2
