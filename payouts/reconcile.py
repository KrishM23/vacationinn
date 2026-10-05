"""Match bank deposits to reservations.

How each channel pays us (from the brief):
    Airbnb  - accommodation + cleaning - fee, ~1 day after check-in. Can combine payouts.
              Bank description has an Airbnb payout id (G-XXXX) that does NOT tell us the reservation,
              so we have to match on amount + date.
    Vrbo    - accommodation + cleaning + taxes - fee, ~1 day after check-out.
              Bank description contains the Vrbo confirmation code (HA-XXXX), so this is easy.
    Direct  - total charged - (2.9% + $0.30), settled in a weekly batch every Monday.

The matching runs in passes, from most certain to least certain:
    1. Direct:  weekly GuestyPay batches by settlement Monday
    2. Vrbo:    by confirmation code in the description
    3. Airbnb:  a) refunded reservations: payout + later negative adjustment
                b) exact amount, one reservation, closest date wins
                c) exact amount, 2-3 reservations combined into one payout
                d) within $1.00 of one reservation (flagged as a variance)
Whatever is left over becomes an issue with a reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations
from typing import Dict, List, Optional, Set

from .models import ZERO, Deposit, OwnerAgreement, Reservation
from .payouts import channel_fee, gross_rental

AIRBNB_DATE_WINDOW_DAYS = 3        # how far off "~1 day after check-in" we'll accept
VARIANCE_TOLERANCE = Decimal("1.00")
MAX_COMBINED_PAYOUTS = 3
CHANNEL_NAMES = {"airbnb": "Airbnb", "vrbo": "Vrbo", "direct": "Direct"}


# ---------------------------------------------------------------------------
# What we expect to see in the bank for each reservation
# ---------------------------------------------------------------------------

def next_monday_after(day: date) -> date:
    """GuestyPay settles on Mondays. A charge made on a Monday goes into the NEXT Monday's batch."""
    days_ahead = 7 - day.weekday()   # Monday is weekday 0
    return day + timedelta(days=days_ahead)


@dataclass
class ExpectedPayout:
    reservation: Reservation
    amount: Decimal
    expected_date: date


def expected_payout(res: Reservation) -> ExpectedPayout:
    """Net amount we expect the channel to send for this reservation, after any refund."""
    fee = channel_fee(res)
    if res.channel == "airbnb":
        amount = gross_rental(res) + res.cleaning_fee - fee
        when = res.check_in + timedelta(days=1)
    elif res.channel == "vrbo":
        amount = gross_rental(res) + res.cleaning_fee + res.taxes_collected - fee
        when = res.check_out + timedelta(days=1)
    elif res.channel == "direct":
        amount = gross_rental(res) + res.cleaning_fee + res.taxes_collected - fee
        when = next_monday_after(res.check_in)
    else:
        raise ValueError(f"Unknown channel {res.channel!r} on {res.reservation_id}")
    return ExpectedPayout(res, amount, when)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class Match:
    kind: str
    deposits: List[Deposit]
    reservations: List[Reservation]
    expected_total: Decimal
    note: str = ""

    @property
    def deposited_total(self) -> Decimal:
        return sum((d.amount for d in self.deposits), ZERO)

    @property
    def difference(self) -> Decimal:
        return self.deposited_total - self.expected_total


@dataclass
class Issue:
    severity: str      # "action" = someone needs to look at it, "info" = FYI only
    category: str
    reference: str     # transaction id(s) and/or reservation id(s)
    amount: Decimal
    reason: str


@dataclass
class ReconciliationResult:
    matches: List[Match] = field(default_factory=list)
    issues: List[Issue] = field(default_factory=list)
    reservation_status: Dict[str, str] = field(default_factory=dict)
    unfunded_ids: Set[str] = field(default_factory=set)
    total_deposits: Decimal = ZERO

    @property
    def total_matched(self) -> Decimal:
        return sum((m.deposited_total for m in self.matches), ZERO)

    @property
    def action_items(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == "action"]

    @property
    def info_items(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == "info"]


# ---------------------------------------------------------------------------
# The matcher
# ---------------------------------------------------------------------------

def money(x: Decimal) -> str:
    return f"${x:,.2f}" if x >= 0 else f"-${-x:,.2f}"


class Reconciler:
    def __init__(self, reservations: List[Reservation], deposits: List[Deposit],
                 agreements: Dict[str, OwnerAgreement], year: int, month: int):
        self.agreements = agreements
        self.year = year
        self.month = month
        self.all_deposits = list(deposits)
        self.expected = {r.reservation_id: expected_payout(r) for r in reservations}

        # Things still waiting to be matched. We remove from these as we go.
        self.open_deposits: List[Deposit] = sorted(deposits, key=lambda d: (d.date, d.transaction_id))
        self.open_reservations: List[Reservation] = []

        self.result = ReconciliationResult(total_deposits=sum((d.amount for d in deposits), ZERO))

        for res in reservations:
            if self.expected[res.reservation_id].amount == ZERO:
                self.result.reservation_status[res.reservation_id] = "No deposit expected"
                self.result.issues.append(Issue(
                    "info", "No deposit expected", res.reservation_id, ZERO,
                    f"{res.status.title()} {CHANNEL_NAMES.get(res.channel, res.channel)} booking, refunded {money(res.refund_amount)} of "
                    f"{money(res.accommodation_total)} - nothing should land in the bank and nothing did."))
            else:
                self.open_reservations.append(res)

    # -- helpers ------------------------------------------------------------

    def _record(self, kind: str, deposits: List[Deposit], reservations: List[Reservation], note: str = ""):
        expected_total = sum((self.expected[r.reservation_id].amount for r in reservations), ZERO)
        match = Match(kind, deposits, reservations, expected_total, note)
        self.result.matches.append(match)
        txn_ids = ", ".join(d.transaction_id for d in deposits)
        for r in reservations:
            self.result.reservation_status[r.reservation_id] = f"Matched ({txn_ids})"
            self.open_reservations.remove(r)
        for d in deposits:
            self.open_deposits.remove(d)
        return match

    def _open(self, channel: str) -> List[Reservation]:
        return [r for r in self.open_reservations if r.channel == channel]

    def _open_deposits(self, source: str) -> List[Deposit]:
        return [d for d in self.open_deposits if d.source == source]

    def _days_off(self, deposit: Deposit, res: Reservation) -> int:
        return abs((deposit.date - self.expected[res.reservation_id].expected_date).days)

    # -- pass 1: direct bookings -------------------------------------------

    def match_direct_batches(self):
        batches: Dict[date, List[Reservation]] = {}
        for res in self._open("direct"):
            batches.setdefault(self.expected[res.reservation_id].expected_date, []).append(res)

        for deposit in self._open_deposits("direct"):
            batch = batches.get(deposit.date)
            if not batch:
                continue
            match = self._record("Weekly GuestyPay batch", [deposit], batch,
                                 f"{len(batch)} direct booking(s) checked in the week before {deposit.date:%a %b %d}")
            if match.difference != ZERO:
                self.result.issues.append(Issue(
                    "action", "Batch amount mismatch", deposit.transaction_id, match.difference,
                    f"GuestyPay batch for {deposit.date} should be {money(match.expected_total)} "
                    f"({', '.join(r.reservation_id for r in batch)}) but {money(deposit.amount)} was deposited."))

    # -- pass 2: vrbo -------------------------------------------------------

    def match_vrbo_by_code(self):
        for res in self._open("vrbo"):
            code = res.channel_confirmation.upper()
            found = [d for d in self._open_deposits("vrbo") if code in d.description.upper()]
            if not found:
                continue
            match = self._record("Vrbo confirmation code", found, [res],
                                 f"Description contains {res.channel_confirmation}")
            if match.difference != ZERO:
                self.result.issues.append(Issue(
                    "action", "Amount mismatch", f"{res.reservation_id} / {', '.join(d.transaction_id for d in found)}",
                    match.difference,
                    f"Vrbo sent {money(match.deposited_total)} but we expected {money(match.expected_total)}."))

    # -- pass 3a: airbnb refunds after payout ---------------------------------

    def match_airbnb_refunds(self):
        for res in self._open("airbnb"):
            if res.refund_amount <= 0:
                continue
            target = self.expected[res.reservation_id].amount
            payouts = [d for d in self._open_deposits("airbnb")
                       if d.amount > 0 and self._days_off(d, res) <= AIRBNB_DATE_WINDOW_DAYS]
            clawbacks = [d for d in self._open_deposits("airbnb") if d.amount < 0]
            pair = next(((p, c) for p in payouts for c in clawbacks
                         if c.date >= p.date and p.amount + c.amount == target), None)
            if pair is None:
                continue
            payout, clawback = pair
            fee_returned = res.refund_amount + clawback.amount
            self._record("Payout + refund adjustment", [payout, clawback], [res],
                         f"Airbnb paid {money(payout.amount)} before the {money(res.refund_amount)} refund, "
                         f"then clawed back {money(-clawback.amount)} "
                         f"(refund less {money(fee_returned)} of host fee Airbnb gave back).")
            self.result.issues.append(Issue(
                "info", "Refund after payout", f"{res.reservation_id} / {payout.transaction_id}, {clawback.transaction_id}",
                clawback.amount,
                f"Refund of {money(res.refund_amount)} came after Airbnb's payout, so it shows up as a negative "
                f"deposit. Net {money(payout.amount + clawback.amount)} matches what the PMS expects. Note the PMS "
                f"platform_fee ({money(channel_fee(res))}) is the post-refund fee, not the fee on the original payout."))

    # -- pass 3b: airbnb, one deposit = one reservation -----------------------

    def match_airbnb_exact(self):
        # Build every (deposit, reservation) pair with the exact amount, then hand them out
        # closest-date-first. This matters when two reservations have the same amount.
        pairs = []
        for d in self._open_deposits("airbnb"):
            for r in self._open("airbnb"):
                days = self._days_off(d, r)
                if d.amount == self.expected[r.reservation_id].amount and days <= AIRBNB_DATE_WINDOW_DAYS:
                    pairs.append((days, d.date, d, r))
        pairs.sort(key=lambda p: (p[0], p[1]))

        for days, _, d, r in pairs:
            if d in self.open_deposits and r in self.open_reservations:
                self._record("Exact amount + date", [d], [r],
                             "Same day as expected" if days == 0 else f"{days} day(s) from expected date")

    # -- pass 3c: airbnb, one deposit = several reservations ------------------

    def match_airbnb_combined(self):
        for d in self._open_deposits("airbnb"):
            if d.amount <= 0:
                continue
            candidates = [r for r in self._open("airbnb") if self._days_off(d, r) <= AIRBNB_DATE_WINDOW_DAYS]
            for size in range(2, MAX_COMBINED_PAYOUTS + 1):
                combo = next((c for c in combinations(candidates, size)
                              if sum(self.expected[r.reservation_id].amount for r in c) == d.amount), None)
                if combo:
                    self._record("Combined Airbnb payout", [d], list(combo),
                                 f"Airbnb combined {size} payouts: " + " + ".join(
                                     f"{r.reservation_id} {money(self.expected[r.reservation_id].amount)}" for r in combo))
                    break

    # -- pass 3d: airbnb, close but not exact ---------------------------------

    def match_airbnb_variance(self):
        for d in self._open_deposits("airbnb"):
            if d.amount <= 0:
                continue
            close = [r for r in self._open("airbnb")
                     if self._days_off(d, r) <= AIRBNB_DATE_WINDOW_DAYS
                     and abs(d.amount - self.expected[r.reservation_id].amount) <= VARIANCE_TOLERANCE]
            if not close:
                continue
            best = min(close, key=lambda r: (abs(d.amount - self.expected[r.reservation_id].amount), self._days_off(d, r)))
            match = self._record("Amount variance", [d], [best], f"Off by {money(d.amount - self.expected[best.reservation_id].amount)}")
            self.result.issues.append(Issue(
                "action", "Amount mismatch", f"{best.reservation_id} / {d.transaction_id}", match.difference,
                f"Airbnb deposited {money(d.amount)} but the reservation works out to "
                f"{money(match.expected_total)} (difference {money(match.difference)}). Matched on date and "
                f"near-identical amount; check the Airbnb payout report for an adjustment or fee rounding."))

    # -- leftovers ------------------------------------------------------------

    def flag_leftover_deposits(self):
        matched = [d for m in self.result.matches for d in m.deposits]
        for d in list(self.open_deposits):
            twin = next((m for m in matched if m.date == d.date and m.amount == d.amount
                         and m.description == d.description), None)
            if twin:
                self.result.issues.append(Issue(
                    "action", "Possible duplicate deposit", d.transaction_id, d.amount,
                    f"Same date, amount and payout id ({d.description}) as {twin.transaction_id}, which already "
                    f"covers its reservation. Either the bank feed doubled it or Airbnb paid twice - check the "
                    f"bank statement; if real, Airbnb will likely claw it back."))
            elif d.source == "unknown":
                hint = self._guest_name_hint(d)
                self.result.issues.append(Issue(
                    "action", "Unidentified deposit", d.transaction_id, d.amount,
                    f"'{d.description}' isn't from Airbnb, Vrbo or GuestyPay, and doesn't match any reservation."
                    f"{hint} Not treated as owner income until someone confirms what it's for."))
            elif d.amount < 0:
                self.result.issues.append(Issue(
                    "action", "Unmatched refund / adjustment", d.transaction_id, d.amount,
                    f"Negative {d.source} deposit that doesn't line up with any refunded reservation."))
            else:
                self.result.issues.append(Issue(
                    "action", "Unmatched deposit", d.transaction_id, d.amount,
                    f"{d.source.title()} deposit with no reservation of that amount near {d.date}."))

    def _guest_name_hint(self, d: Deposit) -> str:
        desc = d.description.upper()
        hits = [r for r in self.expected.values() if r.reservation.guest_surname
                and r.reservation.guest_surname.upper() in desc]
        if not hits:
            return ""
        words = desc.split()
        notes = []
        for h in hits:
            res = h.reservation
            surname = res.guest_surname.upper()
            idx = words.index(surname) if surname in words else -1
            bank_initial = words[idx - 1][0] if idx > 0 else ""
            guest_initial = res.guest_name[0].upper()
            initial_note = "" if bank_initial in ("", guest_initial) else \
                f", but the first initial differs ({bank_initial} vs {guest_initial})"
            notes.append(f"{res.reservation_id} guest {res.guest_name}{initial_note}")
        return (f" Surname matches {'; '.join(notes)}. Could be an off-platform payment from that guest "
                f"or a family member (damage, extra fee, early check-in), or something unrelated.")

    def flag_leftover_reservations(self):
        last_bank_date = max((d.date for d in self.all_deposits), default=None)
        for res in list(self.open_reservations):
            exp = self.expected[res.reservation_id]
            self.result.unfunded_ids.add(res.reservation_id)
            if last_bank_date is not None and exp.expected_date > last_bank_date:
                self.result.reservation_status[res.reservation_id] = "Not due yet"
                self.result.issues.append(Issue(
                    "info", "Deposit not due yet", res.reservation_id, exp.amount,
                    f"Expected around {exp.expected_date}, after the bank feed ends ({last_bank_date})."))
            else:
                self.result.reservation_status[res.reservation_id] = "Missing"
                self.result.issues.append(Issue(
                    "action", "Missing deposit", res.reservation_id, exp.amount,
                    f"Expected {money(exp.amount)} from {res.channel.title()} "
                    f"({res.channel_confirmation}) around {exp.expected_date}; nothing in the bank feed matches. "
                    f"Owner share is held until it arrives."))

    def flag_other_things(self):
        for m in self.result.matches:
            for r in m.reservations:
                if r.listing_id not in self.agreements:
                    self.result.issues.append(Issue(
                        "action", "No owner agreement", f"{r.reservation_id} / {', '.join(d.transaction_id for d in m.deposits)}",
                        self.expected[r.reservation_id].amount,
                        f"Money for {r.listing_name} ({r.listing_id}) is in the bank but there's no owner agreement "
                        f"for this listing, so there's nobody to pay and no commission rate. Holding the funds."))
            for d in m.deposits:
                if (d.date.year, d.date.month) != (self.year, self.month):
                    self.result.issues.append(Issue(
                        "info", "Deposit outside the month", d.transaction_id, d.amount,
                        f"Dated {d.date}, matched to {', '.join(r.reservation_id for r in m.reservations)}. "
                        f"Reconciles fine, but it's {d.date:%B} cash."))
            for r in m.reservations:
                if (r.check_in.year, r.check_in.month) != (self.year, self.month):
                    self.result.issues.append(Issue(
                        "info", "Reservation outside the month", r.reservation_id, self.expected[r.reservation_id].amount,
                        f"Checked in {r.check_in}, so it goes on that month's owner statement, not this one."))

    def run(self) -> ReconciliationResult:
        self.match_direct_batches()
        self.match_vrbo_by_code()
        self.match_airbnb_refunds()
        self.match_airbnb_exact()
        self.match_airbnb_combined()
        self.match_airbnb_variance()
        self.flag_leftover_deposits()
        self.flag_leftover_reservations()
        self.flag_other_things()
        self.result.matches.sort(key=lambda m: (m.deposits[0].date, m.deposits[0].transaction_id))
        return self.result


def reconcile(reservations, deposits, agreements, year, month) -> ReconciliationResult:
    return Reconciler(reservations, deposits, agreements, year, month).run()
