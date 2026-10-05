"""Reconciliation matching rules, each tested with a tiny made-up data set."""
from datetime import date

from helpers import D, make_agreement, make_dep, make_res
from payouts.reconcile import expected_payout, next_monday_after, reconcile

AGREEMENTS = {"L1": make_agreement()}


def run(reservations, deposits, agreements=AGREEMENTS):
    return reconcile(reservations, deposits, agreements, 2026, 9)


def categories(result):
    return sorted(i.category for i in result.issues)


# --- expected amounts ------------------------------------------------------

def test_expected_airbnb_excludes_taxes():
    res = make_res(accommodation_total="200", cleaning_fee="50", taxes_collected="30", platform_fee="7.50")
    exp = expected_payout(res)
    assert exp.amount == D("242.50")
    assert exp.expected_date == date(2026, 9, 11)   # day after check-in


def test_expected_vrbo_includes_taxes_and_pays_after_checkout():
    res = make_res(channel="vrbo", accommodation_total="200", cleaning_fee="50", taxes_collected="30", platform_fee="20")
    exp = expected_payout(res)
    assert exp.amount == D("260.00")
    assert exp.expected_date == date(2026, 9, 13)   # day after check-out


def test_next_monday():
    assert next_monday_after(date(2026, 9, 2)) == date(2026, 9, 7)    # Wed -> Mon
    assert next_monday_after(date(2026, 9, 13)) == date(2026, 9, 14)  # Sun -> Mon
    assert next_monday_after(date(2026, 9, 14)) == date(2026, 9, 21)  # Mon -> next Mon


# --- matching --------------------------------------------------------------

def test_direct_bookings_matched_as_weekly_batch():
    # same two bookings as R1008 + R1011 in the real data
    a = make_res(reservation_id="A", channel="direct", check_in=date(2026, 9, 8), platform_fee=None,
                 accommodation_total="1320.00", cleaning_fee="200.00", taxes_collected="212.80")
    b = make_res(reservation_id="B", channel="direct", check_in=date(2026, 9, 12), platform_fee=None,
                 accommodation_total="555.00", cleaning_fee="120.00", taxes_collected="94.50")
    dep = make_dep("T1", date(2026, 9, 14), "2429.13", "GUESTYPAY SETTLEMENT 0914")
    result = run([a, b], [dep])
    assert len(result.matches) == 1
    assert {r.reservation_id for r in result.matches[0].reservations} == {"A", "B"}
    assert result.issues == []


def test_direct_batch_with_wrong_total_is_flagged():
    a = make_res(channel="direct", check_in=date(2026, 9, 8), platform_fee=None,
                 accommodation_total="100.00", cleaning_fee="0", taxes_collected="0")   # expects 96.80
    dep = make_dep("T1", date(2026, 9, 14), "90.00", "GUESTYPAY SETTLEMENT 0914")
    result = run([a], [dep])
    assert "Batch amount mismatch" in categories(result)


def test_vrbo_matched_by_confirmation_code_not_amount():
    res = make_res(channel="vrbo", channel_confirmation="HA-777", platform_fee="20")  # expects 260.00
    other = make_dep("T0", date(2026, 9, 13), "260.00", "HOMEAWAY VRBO PMT HA-999")
    right = make_dep("T1", date(2026, 9, 13), "260.00", "HOMEAWAY VRBO PMT HA-777")
    result = run([res], [other, right])
    assert result.matches[0].deposits[0].transaction_id == "T1"
    assert "Unmatched deposit" in categories(result)   # HA-999 has no reservation


def test_airbnb_combined_payout():
    a = make_res(reservation_id="A", accommodation_total="780", cleaning_fee="175", platform_fee="28.65")  # 926.35
    b = make_res(reservation_id="B", accommodation_total="360", cleaning_fee="120", platform_fee="14.40")  # 465.60
    dep = make_dep("T1", date(2026, 9, 11), "1391.95")
    result = run([a, b], [dep])
    assert result.matches[0].kind == "Combined Airbnb payout"
    assert result.unfunded_ids == set()


def test_same_amount_reservations_go_to_closest_date():
    # like R1023 / R1024: both 712.95, one checks in a day after the other
    early = make_res(reservation_id="EARLY", check_in=date(2026, 9, 25))
    late = make_res(reservation_id="LATE", check_in=date(2026, 9, 26))
    d1 = make_dep("D27", date(2026, 9, 27), "242.50")
    d2 = make_dep("D26", date(2026, 9, 26), "242.50")
    result = run([late, early], [d1, d2])
    pairs = {m.deposits[0].transaction_id: m.reservations[0].reservation_id for m in result.matches}
    assert pairs == {"D26": "EARLY", "D27": "LATE"}


def test_deposit_too_far_from_expected_date_is_not_matched():
    res = make_res(check_in=date(2026, 9, 1))
    dep = make_dep("T1", date(2026, 9, 20), "242.50")
    result = run([res], [dep])
    assert result.matches == []
    assert "Missing deposit" in categories(result)
    assert "Unmatched deposit" in categories(result)


def test_duplicate_deposit_flagged():
    res = make_res()
    d1 = make_dep("T1", date(2026, 9, 11), "242.50", "AIRBNB PAYMENTS G-DUP")
    d2 = make_dep("T2", date(2026, 9, 11), "242.50", "AIRBNB PAYMENTS G-DUP")
    result = run([res], [d1, d2])
    dup = [i for i in result.issues if i.category == "Possible duplicate deposit"]
    assert len(dup) == 1 and dup[0].reference == "T2"


def test_refund_after_payout_matches_payout_plus_clawback():
    # R1014: Airbnb paid 693.55 then clawed back 150.35 ; PMS says net should be 543.20
    res = make_res(accommodation_total="620.00", cleaning_fee="95.00", platform_fee="16.80",
                   refund_amount="155.00", check_in=date(2026, 9, 16))
    pay = make_dep("PAY", date(2026, 9, 17), "693.55")
    adj = make_dep("ADJ", date(2026, 9, 22), "-150.35", "AIRBNB PAYMENTS ADJ G-RF")
    result = run([res], [pay, adj])
    assert len(result.matches) == 1
    assert result.matches[0].difference == D("0")
    assert categories(result) == ["Refund after payout"]


def test_small_variance_matched_but_flagged():
    res = make_res()                                  # expects 242.50
    dep = make_dep("T1", date(2026, 9, 11), "242.52")
    result = run([res], [dep])
    assert result.matches[0].kind == "Amount variance"
    assert result.matches[0].difference == D("0.02")
    assert "Amount mismatch" in categories(result)


def test_big_difference_is_not_a_variance():
    res = make_res()
    dep = make_dep("T1", date(2026, 9, 11), "250.00")
    result = run([res], [dep])
    assert result.matches == []


def test_unknown_deposit_mentions_guest_with_same_surname():
    res = make_res(guest_name="B. Okafor")
    dep = make_dep("T1", date(2026, 9, 16), "450.00", "ZELLE FROM K OKAFOR")
    result = run([res], [dep])
    issue = next(i for i in result.issues if i.category == "Unidentified deposit")
    assert "B. Okafor" in issue.reason
    assert "K vs B" in issue.reason


def test_missing_vs_not_due_yet():
    missing = make_res(reservation_id="MISS", channel="vrbo", channel_confirmation="HA-1",
                       check_in=date(2026, 9, 18), check_out=date(2026, 9, 22))
    future = make_res(reservation_id="FUTURE", channel="vrbo", channel_confirmation="HA-2",
                      check_in=date(2026, 9, 28), check_out=date(2026, 10, 4))
    last_bank_day = make_dep("T1", date(2026, 9, 28), "1.00", "GUESTYPAY SETTLEMENT 0928")
    result = run([missing, future], [last_bank_day])
    assert result.reservation_status["MISS"] == "Missing"
    assert result.reservation_status["FUTURE"] == "Not due yet"
    assert result.unfunded_ids == {"MISS", "FUTURE"}


def test_cancelled_zero_reservation_expects_nothing():
    res = make_res(status="cancelled", accommodation_total="765", refund_amount="765",
                   cleaning_fee="0", platform_fee="0")
    result = run([res], [])
    assert result.reservation_status["R1"] == "No deposit expected"
    assert result.action_items == []


def test_money_for_listing_without_agreement_is_flagged():
    res = make_res(listing_id="L404")
    dep = make_dep("T1", date(2026, 9, 11), "242.50")
    result = run([res], [dep])
    assert len(result.matches) == 1
    assert "No owner agreement" in categories(result)
