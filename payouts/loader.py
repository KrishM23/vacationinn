"""Reads the CSV files into the data classes in models.py."""
from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional

from .models import ZERO, Deposit, OwnerAgreement, Reservation


def parse_money(text: str) -> Optional[Decimal]:
    """'1,234.50' -> Decimal('1234.50'). Blank -> None."""
    text = (text or "").strip().replace("$", "").replace(",", "")
    if text == "":
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValueError(f"Not a valid money amount: {text!r}")


def money_or_zero(text: str) -> Decimal:
    value = parse_money(text)
    return ZERO if value is None else value


def _read_rows(path: Path) -> List[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        # strip stray whitespace from every header and value
        return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def load_reservations(path: Path) -> List[Reservation]:
    reservations = []
    for row in _read_rows(path):
        reservations.append(Reservation(
            reservation_id=row["reservation_id"],
            listing_id=row["listing_id"],
            listing_name=row["listing_name"],
            channel=row["channel"].lower(),
            channel_confirmation=row["channel_confirmation"],
            guest_name=row["guest_name"],
            check_in=date.fromisoformat(row["check_in"]),
            check_out=date.fromisoformat(row["check_out"]),
            nights=int(row["nights"]),
            nightly_rate=money_or_zero(row["nightly_rate"]),
            accommodation_total=money_or_zero(row["accommodation_total"]),
            cleaning_fee=money_or_zero(row["cleaning_fee"]),
            taxes_collected=money_or_zero(row["taxes_collected"]),
            taxes_remitted_by=row["taxes_remitted_by"].lower(),
            platform_fee=parse_money(row["platform_fee"]),
            status=row["status"].lower(),
            refund_amount=money_or_zero(row["refund_amount"]),
        ))
    return reservations


def load_agreements(path: Path) -> Dict[str, OwnerAgreement]:
    """Returns {listing_id: OwnerAgreement}."""
    agreements = {}
    for row in _read_rows(path):
        agreements[row["listing_id"]] = OwnerAgreement(
            listing_id=row["listing_id"],
            listing_name=row["listing_name"],
            owner_name=row["owner_name"],
            owner_email=row["owner_email"],
            commission_pct=Decimal(row["commission_pct"]),
            cleaning_fee_to=row["cleaning_fee_to"].lower(),
        )
    return agreements


def load_deposits(path: Path) -> List[Deposit]:
    deposits = []
    for row in _read_rows(path):
        deposits.append(Deposit(
            transaction_id=row["transaction_id"],
            date=date.fromisoformat(row["date"]),
            amount=money_or_zero(row["amount"]),
            description=row["description"],
        ))
    return deposits


def load_all(data_dir: Path):
    data_dir = Path(data_dir)
    return (
        load_reservations(data_dir / "reservations.csv"),
        load_agreements(data_dir / "owner_agreements.csv"),
        load_deposits(data_dir / "bank_deposits.csv"),
    )
