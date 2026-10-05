"""Small factories so tests can build a reservation / deposit in one line."""
from datetime import date
from decimal import Decimal

from payouts.models import Deposit, OwnerAgreement, Reservation


def D(x) -> Decimal:
    return Decimal(str(x))


def make_res(**kw) -> Reservation:
    values = dict(
        reservation_id="R1", listing_id="L1", listing_name="Test House", channel="airbnb",
        channel_confirmation="HM1", guest_name="A. Guest",
        check_in=date(2026, 9, 10), check_out=date(2026, 9, 12), nights=2,
        nightly_rate=D("100.00"), accommodation_total=D("200.00"), cleaning_fee=D("50.00"),
        taxes_collected=D("30.00"), taxes_remitted_by="channel", platform_fee=D("7.50"),
        status="confirmed", refund_amount=D("0.00"),
    )
    for k, v in kw.items():
        values[k] = D(v) if isinstance(v, (int, float, str)) and k in _MONEY else v
    return Reservation(**values)


_MONEY = {"nightly_rate", "accommodation_total", "cleaning_fee", "taxes_collected", "platform_fee", "refund_amount"}


def make_agreement(**kw) -> OwnerAgreement:
    values = dict(listing_id="L1", listing_name="Test House", owner_name="Pat Owner",
                  owner_email="pat@example.com", commission_pct=D("0.20"), cleaning_fee_to="company")
    values.update(kw)
    if not isinstance(values["commission_pct"], Decimal):
        values["commission_pct"] = D(values["commission_pct"])
    return OwnerAgreement(**values)


def make_dep(txn, day, amount, desc="AIRBNB PAYMENTS G-TEST") -> Deposit:
    return Deposit(txn, day, D(amount), desc)
