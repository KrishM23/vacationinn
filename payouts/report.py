"""Glue: load the files, reconcile, build statements. Used by both the CLI and the web app."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from .loader import load_all
from .models import Deposit, OwnerAgreement, Reservation
from .payouts import StatementRun, build_statements
from .reconcile import ReconciliationResult, reconcile


@dataclass
class MonthReport:
    year: int
    month: int
    reservations: List[Reservation]
    agreements: Dict[str, OwnerAgreement]
    deposits: List[Deposit]
    statements: StatementRun
    reconciliation: ReconciliationResult

    @property
    def period(self) -> str:
        return f"{self.year}-{self.month:02d}"


def run_month(data_dir: Path, year: int, month: int) -> MonthReport:
    reservations, agreements, deposits = load_all(data_dir)
    # Reconcile first so the statements know which reservations haven't been paid to us yet.
    recon = reconcile(reservations, deposits, agreements, year, month)
    statements = build_statements(reservations, agreements, year, month, recon.unfunded_ids)
    return MonthReport(year, month, reservations, agreements, deposits, statements, recon)


def write_csvs(report: MonthReport, out_dir: Path) -> List[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    def write(name, header, rows):
        path = out_dir / name
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
        written.append(path)

    write(f"owner_totals_{report.period}.csv",
          ["owner_name", "owner_email", "total_earned", "held_funds_not_received", "payable_now"],
          [[s.owner_name, s.owner_email, s.total_earned, s.total_held, s.total_payable]
           for s in report.statements.statements])

    rows = []
    for s in report.statements.statements:
        for listing in s.listings:
            for l in listing.lines:
                r = l.reservation
                rows.append([s.owner_name, listing.listing_id, listing.listing_name, r.reservation_id, r.channel,
                             r.guest_name, r.check_in, r.check_out, r.accommodation_total, r.refund_amount,
                             l.gross_rental, l.channel_fee, l.net_rental, listing.commission_pct, l.commission,
                             l.cleaning_to_owner, l.owner_payout,
                             "yes" if l.funds_received else "no", l.note])
    write(f"owner_statement_lines_{report.period}.csv",
          ["owner_name", "listing_id", "listing_name", "reservation_id", "channel", "guest_name", "check_in",
           "check_out", "accommodation_total", "refund_amount", "gross_rental", "channel_fee", "net_rental",
           "commission_pct", "commission", "cleaning_to_owner", "owner_payout", "funds_received", "note"],
          rows)

    write(f"excluded_reservations_{report.period}.csv",
          ["reservation_id", "listing_id", "check_in", "reason"],
          [[e.reservation.reservation_id, e.reservation.listing_id, e.reservation.check_in, e.reason]
           for e in report.statements.excluded])

    write(f"reconciliation_matches_{report.period}.csv",
          ["match_type", "transaction_ids", "deposit_dates", "deposited", "reservation_ids", "expected",
           "difference", "note"],
          [[m.kind, " ".join(d.transaction_id for d in m.deposits), " ".join(str(d.date) for d in m.deposits),
            m.deposited_total, " ".join(r.reservation_id for r in m.reservations), m.expected_total,
            m.difference, m.note] for m in report.reconciliation.matches])

    write(f"reconciliation_exceptions_{report.period}.csv",
          ["severity", "category", "reference", "amount", "reason"],
          [[i.severity, i.category, i.reference, i.amount, i.reason] for i in report.reconciliation.issues])

    return written
