"""Owner payout math and monthly owner statements.

The business rules, per reservation:
    gross rental   = accommodation_total - refund_amount
    channel fee    = platform_fee (Airbnb / Vrbo) or 2.9% + $0.30 of total charged (direct)
    commission     = commission_pct * (gross rental - channel fee)
    cleaning       = cleaning_fee, but only if the agreement says cleaning_fee_to = owner
    owner payout   = gross rental - channel fee - commission + cleaning
Taxes are never owner money.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Set

from .models import ZERO, OwnerAgreement, Reservation, to_cents

GUESTYPAY_PCT = Decimal("0.029")
GUESTYPAY_FLAT = Decimal("0.30")


def total_charged(res: Reservation) -> Decimal:
    """What the guest was charged: accommodation + cleaning + taxes."""
    return res.accommodation_total + res.cleaning_fee + res.taxes_collected


def guestypay_fee(res: Reservation) -> Decimal:
    """2.9% + $0.30 of the total charged, rounded to the cent."""
    return to_cents(total_charged(res) * GUESTYPAY_PCT + GUESTYPAY_FLAT)


def channel_fee(res: Reservation) -> Decimal:
    if res.channel == "direct":
        return guestypay_fee(res)
    return res.platform_fee if res.platform_fee is not None else ZERO


def gross_rental(res: Reservation) -> Decimal:
    return res.accommodation_total - res.refund_amount


@dataclass
class PayoutLine:
    """One reservation on an owner statement, with every step of the math shown."""
    reservation: Reservation
    agreement: OwnerAgreement
    gross_rental: Decimal
    channel_fee: Decimal
    net_rental: Decimal          # gross rental - channel fee (what commission is charged on)
    commission: Decimal
    cleaning_to_owner: Decimal
    owner_payout: Decimal
    funds_received: bool = True  # filled in from the reconciliation
    note: str = ""


def calculate_payout(res: Reservation, agreement: OwnerAgreement) -> PayoutLine:
    gross = gross_rental(res)
    fee = channel_fee(res)
    net = gross - fee
    commission = to_cents(agreement.commission_pct * net)
    cleaning = res.cleaning_fee if agreement.cleaning_fee_to == "owner" else ZERO
    payout = net - commission + cleaning

    notes = []
    if res.status == "cancelled":
        notes.append("Cancelled")
    if res.refund_amount > 0:
        notes.append(f"Refund of ${res.refund_amount:,.2f} deducted")

    return PayoutLine(
        reservation=res,
        agreement=agreement,
        gross_rental=gross,
        channel_fee=fee,
        net_rental=net,
        commission=commission,
        cleaning_to_owner=cleaning,
        owner_payout=payout,
        note="; ".join(notes),
    )


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------

def belongs_to_month(res: Reservation, year: int, month: int) -> bool:
    """A reservation goes on the statement for the month it checks IN.
    (See README - this matches when Airbnb and direct money actually arrives.)"""
    return res.check_in.year == year and res.check_in.month == month


@dataclass
class ListingSection:
    listing_id: str
    listing_name: str
    commission_pct: Decimal
    cleaning_fee_to: str
    lines: List[PayoutLine] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((l.owner_payout for l in self.lines), ZERO)


@dataclass
class OwnerStatement:
    owner_name: str
    owner_email: str
    year: int
    month: int
    listings: List[ListingSection] = field(default_factory=list)

    @property
    def lines(self) -> List[PayoutLine]:
        return [line for listing in self.listings for line in listing.lines]

    @property
    def total_earned(self) -> Decimal:
        return sum((l.owner_payout for l in self.lines), ZERO)

    @property
    def total_held(self) -> Decimal:
        """Earned, but we haven't received the money from the channel yet."""
        return sum((l.owner_payout for l in self.lines if not l.funds_received), ZERO)

    @property
    def total_payable(self) -> Decimal:
        return self.total_earned - self.total_held

    @property
    def slug(self) -> str:
        return "".join(c if c.isalnum() else "-" for c in self.owner_name.lower()).strip("-")


@dataclass
class ExcludedReservation:
    reservation: Reservation
    reason: str


@dataclass
class StatementRun:
    statements: List[OwnerStatement]
    excluded: List[ExcludedReservation]

    @property
    def grand_total_earned(self) -> Decimal:
        return sum((s.total_earned for s in self.statements), ZERO)

    @property
    def grand_total_payable(self) -> Decimal:
        return sum((s.total_payable for s in self.statements), ZERO)

    @property
    def grand_total_held(self) -> Decimal:
        return sum((s.total_held for s in self.statements), ZERO)

    def statement_for(self, slug: str) -> Optional[OwnerStatement]:
        for s in self.statements:
            if s.slug == slug:
                return s
        return None


def build_statements(
    reservations: Iterable[Reservation],
    agreements: Dict[str, OwnerAgreement],
    year: int,
    month: int,
    unfunded_ids: Optional[Set[str]] = None,
) -> StatementRun:
    """Group payout lines by owner -> listing.

    unfunded_ids: reservation ids where the reconciliation couldn't find the
    money in the bank. Those lines still show up but are marked as held.
    """
    unfunded_ids = unfunded_ids or set()
    owners: Dict[str, OwnerStatement] = {}
    excluded: List[ExcludedReservation] = []

    for res in sorted(reservations, key=lambda r: (r.check_in, r.reservation_id)):
        if not belongs_to_month(res, year, month):
            excluded.append(ExcludedReservation(
                res, f"Checks in {res.check_in.isoformat()}, outside {year}-{month:02d} - belongs on that month's statement"))
            continue

        agreement = agreements.get(res.listing_id)
        if agreement is None:
            excluded.append(ExcludedReservation(
                res, f"No owner agreement for listing {res.listing_id} ({res.listing_name}) - can't calculate payout"))
            continue

        line = calculate_payout(res, agreement)
        if res.reservation_id in unfunded_ids:
            line.funds_received = False
            line.note = "; ".join(filter(None, [line.note, "Held - deposit not received yet"]))

        statement = owners.get(agreement.owner_name)
        if statement is None:
            statement = OwnerStatement(agreement.owner_name, agreement.owner_email, year, month)
            owners[agreement.owner_name] = statement

        section = next((s for s in statement.listings if s.listing_id == res.listing_id), None)
        if section is None:
            section = ListingSection(res.listing_id, agreement.listing_name,
                                     agreement.commission_pct, agreement.cleaning_fee_to)
            statement.listings.append(section)
        section.lines.append(line)

    statements = sorted(owners.values(), key=lambda s: s.owner_name)
    for s in statements:
        s.listings.sort(key=lambda l: l.listing_id)
    return StatementRun(statements, excluded)
