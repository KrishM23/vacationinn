"""Plain data classes for the three input files, plus a couple of money helpers.

All money is stored as Decimal (never float) so the cents always add up.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def to_cents(value: Decimal) -> Decimal:
    """Round to the nearest cent, halves round up (like a calculator would)."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Reservation:
    reservation_id: str
    listing_id: str
    listing_name: str
    channel: str               # "airbnb", "vrbo" or "direct"
    channel_confirmation: str
    guest_name: str
    check_in: date
    check_out: date
    nights: int
    nightly_rate: Decimal
    accommodation_total: Decimal
    cleaning_fee: Decimal
    taxes_collected: Decimal
    taxes_remitted_by: str     # "channel" or "company"
    platform_fee: Optional[Decimal]  # blank for direct bookings
    status: str                # "confirmed" or "cancelled"
    refund_amount: Decimal

    @property
    def guest_surname(self) -> str:
        return self.guest_name.split()[-1] if self.guest_name else ""


@dataclass
class OwnerAgreement:
    listing_id: str
    listing_name: str
    owner_name: str
    owner_email: str
    commission_pct: Decimal    # e.g. 0.20 means 20%
    cleaning_fee_to: str       # "owner" or "company"


@dataclass
class Deposit:
    transaction_id: str
    date: date
    amount: Decimal
    description: str

    @property
    def source(self) -> str:
        """Which channel the bank description says this came from."""
        desc = self.description.upper()
        if "AIRBNB" in desc:
            return "airbnb"
        if "VRBO" in desc or "HOMEAWAY" in desc:
            return "vrbo"
        if "GUESTYPAY" in desc:
            return "direct"
        return "unknown"
