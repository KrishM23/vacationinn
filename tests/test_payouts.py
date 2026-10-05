"""Owner payout math. Expected numbers are worked out by hand in the comments."""
from datetime import date

from helpers import D, make_agreement, make_res
from payouts.loader import parse_money
from payouts.payouts import build_statements, calculate_payout, channel_fee, guestypay_fee


def test_airbnb_cleaning_kept_by_company():
    # R1006 from the real data: 820 accommodation, 29.10 Airbnb fee, 20% commission
    res = make_res(accommodation_total="820.00", cleaning_fee="150.00", platform_fee="29.10")
    line = calculate_payout(res, make_agreement(commission_pct="0.20", cleaning_fee_to="company"))
    # net = 820 - 29.10 = 790.90 ; commission = 158.18 ; payout = 632.72
    assert line.net_rental == D("790.90")
    assert line.commission == D("158.18")
    assert line.cleaning_to_owner == D("0")
    assert line.owner_payout == D("632.72")


def test_cleaning_fee_goes_to_owner_when_agreement_says_so():
    # R1022: 1365 accommodation, 44.55 fee, 22%, 120 cleaning to owner
    res = make_res(accommodation_total="1365.00", cleaning_fee="120.00", platform_fee="44.55")
    line = calculate_payout(res, make_agreement(commission_pct="0.22", cleaning_fee_to="owner"))
    # commission = 0.22 * 1320.45 = 290.499 -> 290.50 ; payout = 1320.45 - 290.50 + 120 = 1149.95
    assert line.commission == D("290.50")
    assert line.owner_payout == D("1149.95")


def test_taxes_never_reach_the_owner():
    a = make_res(taxes_collected="0.00")
    b = make_res(taxes_collected="999.99")
    agreement = make_agreement(cleaning_fee_to="owner")
    # (for Airbnb/Vrbo taxes don't change the platform fee, so the payout must be identical)
    assert calculate_payout(a, agreement).owner_payout == calculate_payout(b, agreement).owner_payout


def test_direct_booking_fee_is_2_9_pct_plus_30_cents_of_total_charged():
    # R1003: 290 + 95 + 53.90 = 438.90 charged ; 2.9% = 12.7281 + 0.30 = 13.0281 -> 13.03
    res = make_res(channel="direct", accommodation_total="290.00", cleaning_fee="95.00",
                   taxes_collected="53.90", platform_fee=None)
    assert guestypay_fee(res) == D("13.03")
    assert channel_fee(res) == D("13.03")
    line = calculate_payout(res, make_agreement(commission_pct="0.18"))
    # net 276.97 ; commission 49.8546 -> 49.85 ; payout 227.12
    assert line.owner_payout == D("227.12")


def test_direct_fee_ignores_platform_fee_column():
    res = make_res(channel="direct", platform_fee="500.00",
                   accommodation_total="100.00", cleaning_fee="0.00", taxes_collected="0.00")
    assert channel_fee(res) == D("3.20")   # 2.90 + 0.30


def test_missing_platform_fee_counts_as_zero():
    res = make_res(platform_fee=None)
    assert channel_fee(res) == D("0")


def test_partial_refund_reduces_gross_rental():
    # R1014: 620 - 155 refund = 465 ; fee 16.80 ; 18% of 448.20 = 80.676 -> 80.68 ; payout 367.52
    res = make_res(accommodation_total="620.00", refund_amount="155.00", cleaning_fee="95.00", platform_fee="16.80")
    line = calculate_payout(res, make_agreement(commission_pct="0.18"))
    assert line.gross_rental == D("465.00")
    assert line.owner_payout == D("367.52")
    assert "Refund" in line.note


def test_cancelled_fully_refunded_pays_nothing():
    res = make_res(channel="vrbo", status="cancelled", accommodation_total="765.00",
                   refund_amount="765.00", cleaning_fee="0.00", taxes_collected="0.00", platform_fee="0.00")
    line = calculate_payout(res, make_agreement())
    assert line.owner_payout == D("0.00")


def test_commission_rounds_half_up():
    # 0.25 * 10.02 = 2.505 -> 2.51 (banker's rounding would give 2.50)
    res = make_res(accommodation_total="10.02", platform_fee="0.00", cleaning_fee="0.00")
    line = calculate_payout(res, make_agreement(commission_pct="0.25"))
    assert line.commission == D("2.51")


def test_parse_money_handles_blanks_and_formatting():
    assert parse_money("") is None
    assert parse_money(" $1,234.50 ") == D("1234.50")


# --- statements ------------------------------------------------------------

def test_statement_uses_check_in_month_and_skips_unknown_listings():
    reservations = [
        make_res(reservation_id="AUG", check_in=date(2026, 8, 30), check_out=date(2026, 9, 2)),
        make_res(reservation_id="SEP", check_in=date(2026, 9, 5)),
        make_res(reservation_id="OCT_OUT", check_in=date(2026, 9, 29), check_out=date(2026, 10, 3)),
        make_res(reservation_id="ORPHAN", listing_id="L999"),
    ]
    run = build_statements(reservations, {"L1": make_agreement()}, 2026, 9)
    on_statement = [l.reservation.reservation_id for l in run.statements[0].lines]
    excluded = {e.reservation.reservation_id: e.reason for e in run.excluded}

    assert on_statement == ["SEP", "OCT_OUT"]
    assert set(excluded) == {"AUG", "ORPHAN"}
    assert "No owner agreement" in excluded["ORPHAN"]


def test_statement_groups_by_owner_and_listing_and_holds_unfunded():
    agreements = {
        "L1": make_agreement(listing_id="L1", owner_name="Same Owner"),
        "L2": make_agreement(listing_id="L2", listing_name="Other House", owner_name="Same Owner"),
    }
    reservations = [
        make_res(reservation_id="A", listing_id="L1"),
        make_res(reservation_id="B", listing_id="L2"),
        make_res(reservation_id="C", listing_id="L2"),
    ]
    run = build_statements(reservations, agreements, 2026, 9, unfunded_ids={"C"})
    assert len(run.statements) == 1
    s = run.statements[0]
    assert [l.listing_id for l in s.listings] == ["L1", "L2"]
    per_line = calculate_payout(reservations[0], agreements["L1"]).owner_payout
    assert s.total_earned == per_line * 3
    assert s.total_held == per_line
    assert s.total_payable == per_line * 2


def test_slug_is_url_friendly():
    run = build_statements([make_res()], {"L1": make_agreement(owner_name="Tom & Lisa Becker")}, 2026, 9)
    assert run.statements[0].slug == "tom-lisa-becker"
