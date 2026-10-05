"""Command line entry point.

    python -m payouts.cli                       # September 2026, files in ./data, CSVs to ./output
    python -m payouts.cli --month 2026-09 --data data --out output
    python -m payouts.cli --owner "Dana Whitfield"   # show one owner's statement in detail
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .report import run_month, write_csvs


def fmt(x) -> str:
    return f"{x:>12,.2f}"


def print_statement(s):
    print(f"\n=== {s.owner_name} <{s.owner_email}> ===")
    for listing in s.listings:
        print(f"  {listing.listing_id} {listing.listing_name}  "
              f"(commission {listing.commission_pct * 100:.0f}%, cleaning fee to {listing.cleaning_fee_to})")
        print(f"    {'res':<6}{'channel':<8}{'check-in':<12}{'gross':>12}{'ch. fee':>12}"
              f"{'commission':>12}{'cleaning':>12}{'payout':>12}")
        for l in listing.lines:
            r = l.reservation
            print(f"    {r.reservation_id:<6}{r.channel:<8}{str(r.check_in):<12}{fmt(l.gross_rental)}"
                  f"{fmt(l.channel_fee)}{fmt(-l.commission)}{fmt(l.cleaning_to_owner)}{fmt(l.owner_payout)}"
                  f"  {l.note}")
        print(f"    {'listing total':<62}{fmt(listing.total)}")
    print(f"  Earned: {s.total_earned:,.2f}   Held: {s.total_held:,.2f}   Payable now: {s.total_payable:,.2f}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Owner statements + bank reconciliation")
    parser.add_argument("--month", default="2026-09", help="YYYY-MM (default 2026-09)")
    parser.add_argument("--data", default="data", help="folder with the three CSV files")
    parser.add_argument("--out", default="output", help="folder to write result CSVs to")
    parser.add_argument("--owner", help="only print this owner's statement")
    args = parser.parse_args(argv)

    try:
        year, month = (int(x) for x in args.month.split("-"))
    except ValueError:
        parser.error("--month must look like 2026-09")

    report = run_month(Path(args.data), year, month)
    run, recon = report.statements, report.reconciliation

    statements = run.statements
    if args.owner:
        statements = [s for s in statements if s.owner_name.lower() == args.owner.lower()]
        if not statements:
            print(f"No owner named {args.owner!r}. Owners: {', '.join(s.owner_name for s in run.statements)}")
            return 1

    print(f"OWNER STATEMENTS - {report.period}")
    for s in statements:
        print_statement(s)

    if not args.owner:
        print("\n--- Owner totals ---")
        for s in run.statements:
            print(f"  {s.owner_name:<22}{fmt(s.total_earned)} earned {fmt(s.total_held)} held {fmt(s.total_payable)} payable")
        print(f"  {'TOTAL':<22}{fmt(run.grand_total_earned)} earned {fmt(run.grand_total_held)} held "
              f"{fmt(run.grand_total_payable)} payable")

        print("\n--- Left off the statements ---")
        for e in run.excluded:
            print(f"  {e.reservation.reservation_id}: {e.reason}")

        print(f"\nRECONCILIATION - {report.period}")
        print(f"  Deposits in bank feed: {len(report.deposits)} totalling {recon.total_deposits:,.2f}")
        print(f"  Matched to reservations: {sum(len(m.deposits) for m in recon.matches)} totalling {recon.total_matched:,.2f}")
        print(f"  Not matched: {recon.total_deposits - recon.total_matched:,.2f}")

        print(f"\n--- Needs attention ({len(recon.action_items)}) ---")
        for i in recon.action_items:
            print(f"  [{i.category}] {i.reference} {i.amount:,.2f}\n      {i.reason}")
        print(f"\n--- FYI ({len(recon.info_items)}) ---")
        for i in recon.info_items:
            print(f"  [{i.category}] {i.reference} {i.amount:,.2f}\n      {i.reason}")

        paths = write_csvs(report, Path(args.out))
        print(f"\nWrote {len(paths)} CSV files to {args.out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
