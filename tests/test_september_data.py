"""End-to-end check on the real September files.

These numbers were worked out by hand from the CSVs first (see README),
so if any rule changes by accident these tests will catch it.
"""
from pathlib import Path

import pytest

from helpers import D
from payouts.report import run_month, write_csvs

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="module")
def report():
    return run_month(DATA, 2026, 9)


def test_owner_totals(report):
    totals = {s.owner_name: (s.total_earned, s.total_held, s.total_payable) for s in report.statements.statements}
    assert totals == {
        "Dana Whitfield":    (D("3845.55"), D("0.00"), D("3845.55")),
        "Marcus Oyelaran":   (D("4691.94"), D("0.00"), D("4691.94")),
        "Priya Raman":       (D("1811.30"), D("0.00"), D("1811.30")),
        "Tom & Lisa Becker": (D("2732.67"), D("657.89"), D("2074.78")),   # R1016 Vrbo money never arrived
    }
    assert report.statements.grand_total_earned == D("13081.46")


def test_excluded_reservations(report):
    assert {e.reservation.reservation_id for e in report.statements.excluded} == {"R1001", "R1007", "R1017"}


def test_every_deposit_is_matched_or_explained(report):
    r = report.reconciliation
    matched_ids = {d.transaction_id for m in r.matches for d in m.deposits}
    flagged_ids = {i.reference for i in r.issues}
    for d in report.deposits:
        assert d.transaction_id in matched_ids or d.transaction_id in flagged_ids, d.transaction_id
    assert r.total_deposits == D("22632.15")
    assert r.total_matched == D("20630.15")
    assert r.total_deposits - r.total_matched == D("2002.00")   # duplicate 1552 + zelle 450


def test_every_reservation_has_a_status(report):
    status = report.reconciliation.reservation_status
    assert set(status) == {r.reservation_id for r in report.reservations}
    assert status["R1016"] == "Missing"
    assert status["R1010"] == "No deposit expected"


def test_exceptions_found(report):
    action = sorted((i.category, i.reference) for i in report.reconciliation.action_items)
    assert action == [
        ("Amount mismatch", "R1018 / TXN50238"),
        ("Missing deposit", "R1016"),
        ("No owner agreement", "R1007 / TXN50102"),
        ("No owner agreement", "R1017 / TXN50255"),
        ("Possible duplicate deposit", "TXN50170"),
        ("Unidentified deposit", "TXN50391"),
    ]


def test_specific_tricky_matches(report):
    by_res = {}
    for m in report.reconciliation.matches:
        for r in m.reservations:
            by_res[r.reservation_id] = sorted(d.transaction_id for d in m.deposits)
    assert by_res["R1004"] == by_res["R1005"] == ["TXN50034"]        # combined Airbnb payout
    assert by_res["R1008"] == by_res["R1011"] == ["TXN50136"]        # GuestyPay weekly batch
    assert by_res["R1014"] == ["TXN50187", "TXN50374"]               # payout then refund clawback
    assert by_res["R1023"] == ["TXN50306"]                           # same $712.95, by date
    assert by_res["R1024"] == ["TXN50323"]


def test_csv_export(report, tmp_path):
    paths = write_csvs(report, tmp_path)
    assert len(paths) == 5
    lines = (tmp_path / "owner_statement_lines_2026-09.csv").read_text().splitlines()
    assert len(lines) == 1 + 21   # header + 21 reservations on statements
