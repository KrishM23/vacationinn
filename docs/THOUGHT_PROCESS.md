# My Thought Process & Findings

This is a plain-English walkthrough of how I approached the project, what I found in the data, and why I made the decisions I did. The [README](../README.md) has the technical details. This doc is the story behind them.

---

## 1. First, understanding the business

Before touching any code, I made sure I understood who's who:

- **The homeowner** owns the house and wants rental income without doing the work.
- **Airbnb / Vrbo** are just websites where guests find and book the house.
- **VacationInn (us)** runs the rental for the owner: listing it, pricing, guests, cleaning, repairs. We collect all the money and pay the owner their share, minus our commission.

The money moves like this:

```
Guest pays Airbnb/Vrbo/our website
        ↓
The channel keeps its fee and sends the rest to our bank account
        ↓
We keep our commission (and sometimes the cleaning fee)
        ↓
We pay the rest to the homeowner
```

That gave me the two jobs:

1. **Owner statements:** how much do we owe each owner?
2. **Reconciliation:** did the money we expected actually arrive in the bank?

---

## 2. Looking at the data before writing code

There are three files:

| File | What it has |
|---|---|
| `reservations.csv` | 24 bookings: house, guest, dates, rent, cleaning fee, taxes, channel fee, refunds |
| `owner_agreements.csv` | 5 houses: who owns each one, our commission %, who keeps the cleaning fee |
| `bank_deposits.csv` | 23 deposits that landed in our bank account |

The brief said the data was "intentionally imperfect," so my plan was to **work out the right answers by hand first**, then build the program, and then check that the program got the same answers. That way I'd know the numbers were right, not just that the code ran.

I also did some basic sanity checks:
- Do the nights match the check-in/check-out dates? Yes.
- Does nights × nightly rate = accommodation total? Yes.
- Do the house names match between files? Yes.
- Is every house in the bookings also in the agreements? **No.** (More on that below.)

---

## 3. Working out the owner payout

The brief gives the formula. For each booking:

1. **Gross rental** = rent − any refund
2. Take off the **channel fee** (Airbnb/Vrbo's fee, or 2.9% + $0.30 for direct bookings)
3. Take off **our commission**, which is a % of what's left after the fee
4. **Add the cleaning fee**, but only if the owner's agreement says they get it
5. **Taxes never go to the owner**

**Example: R1006, Silver Lake Bungalow on Airbnb (20% commission, we keep cleaning)**

| Step | Amount |
|---|---:|
| Rent | $820.00 |
| Minus Airbnb fee | −$29.10 |
| Left after fee | $790.90 |
| Minus 20% commission | −$158.18 |
| Cleaning (we keep it) | $0.00 |
| **Owner gets** | **$632.72** |

I did that for every booking and grouped the results by owner, then by house.

---

## 4. Matching the bank to the bookings

This was the hardest part, because **the two files don't share an ID**. The bank has its own transaction IDs (`TXN50068`), and the booking system has its own (`R1006`). So I had to use clues, and the clues depend on who paid:

| Who paid | Clue I used | How confident |
|---|---|---|
| **Vrbo** | The bank description contains the booking code (e.g. `HA-1012`) | Very |
| **Direct (GuestyPay)** | Pays every Monday for the past week's bookings, so I add up that week's bookings and compare | Very |
| **Airbnb** | No usable code, so I use the **amount** and the **date** (Airbnb pays ~1 day after check-in) | Fairly. It's an educated guess. |

For Airbnb I went step by step, from most certain to least:

1. Exact amount, close to the expected date. If two fit, the closest date wins.
2. If nothing fits alone, try adding 2–3 bookings together (Airbnb sometimes combines payouts).
3. If a deposit is within $1 of a booking, match it but flag it.

Whatever was left over (deposits with no booking, or bookings with no deposit) went on the problem list with a reason.

---

## 5. What I found

### Problems that need someone to look at them

**1. Duplicate deposit: $1,552.00 (TXN50170)**
The same Airbnb payment shows up twice, on the same day, with the same amount and the same Airbnb payout code. The first one covers booking R1013. The second one has nothing to match. Either the bank feed doubled it, or Airbnb actually paid twice (and will probably take it back).

**2. Mystery Zelle payment: $450.00 (TXN50391)**
"ZELLE FROM K OKAFOR" isn't from Airbnb, Vrbo or our card processor, and doesn't match any booking. A guest named **B.** Okafor stayed at Silver Lake (R1006), so it *might* be related, maybe a damage fee or an extra charge. But the first initial is different, so I didn't assume anything. It's flagged and not treated as owner money.

**3. Missing Vrbo payout: $932.80 (R1016)**
The Beckers' Mar Vista Cottage had a Vrbo guest who checked out Sept 22. Vrbo pays the day after check-out, so the money should have come around Sept 23. The bank feed goes until Sept 28, and there's nothing for it.
- The $932.80 isn't all owner money. It includes $123.20 of taxes and our $151.71 commission. The owner's share is **$657.89**.
- I **held back** that $657.89 instead of paying it, because we don't have the money yet.

**4. A house with no owner agreement: $1,223.90 (R1007 + R1017)**
Highland Park Casita (L006) has two bookings, and the money from both arrived ($746.90 + $477.00). But L006 isn't in the owner agreements file. So I don't know who owns it, what our commission is, or who keeps the cleaning fee. I didn't guess. Both bookings are left off the statements and flagged. Most likely it's a new house whose agreement just hasn't been entered yet.

**5. 2 cents off: R1018**
Airbnb sent $785.72, but the booking works out to $785.70. Right day, nearly the right amount, so I matched it but flagged the difference. It doesn't change the owner's pay, because I calculate that from the booking itself.

### Things that looked wrong but actually check out

**6. Refund after payout: R1014**
This one took the most figuring out:
- Airbnb paid us **$693.55** on Sept 17.
- The guest got a **$155** refund later.
- On Sept 22 Airbnb took back **$150.35**. That's not $155, because Airbnb returned its 3% fee on the refunded part ($4.65).
- $693.55 − $150.35 = **$543.20**, which is exactly what the booking system says the booking should net. ✅

**7. Combined payout: $1,391.95**
No single booking matched this amount. It turned out to be two Airbnb bookings that checked in the same day (R1004 + R1005: $926.35 + $465.60), paid together.

**8. Two deposits with the same amount: $712.95**
Two bookings (R1023 and R1024) both came to exactly $712.95, and so did two deposits. The amounts alone couldn't tell them apart, so I used the dates: R1023 checked in the 25th and was paid the 26th, and R1024 checked in the 26th and was paid the 27th.

**9. An August booking: R1001**
It checked in Aug 28 and was paid Aug 29, so it belongs on August's statement, not September's. It still reconciles fine.

**10. A cancelled booking: R1010**
It was fully refunded, so the owner gets $0 and nothing should hit the bank. Nothing did. ✅

---

## 6. Decisions I had to make

The rules didn't cover everything, so here's what I decided and why.

**Which month does a booking count in? → The month the guest checks in.**
For Airbnb and direct bookings, that's when the money actually arrives. R1001's money came in August, so it was probably already paid out on August's statement, and counting it again could pay the owner twice. The other options were check-out month, or splitting a booking by nights. I'd confirm this with the business.

**Pay the owner even if we haven't received the money? → No, hold it.**
If we pay the Beckers for R1016 and Vrbo never pays us, we lose that money. Waiting is the safer choice. Their statement still shows the booking, so they can see it.

**Guess an owner for L006? → No.**
Paying the wrong person, or using the wrong commission, is worse than flagging it for a human.

**How to round? → To the cent on each booking, halves round up.**
The totals are sums of the rounded lines, so a statement always adds up on paper. When I calculated the card fees this way, they matched the actual bank deposits to the cent, which told me it was the right approach.

**Use exact decimals, not regular computer numbers.**
Regular "float" numbers can be slightly off (in Python, `0.1 + 0.2` gives `0.30000000000000004`). With money, those tiny errors add up, so everything uses Python's `Decimal`.

---

## 7. Something in the rules I'd question

Airbnb's fee is charged on the **rent plus the cleaning fee**. But the rules take the **whole fee out of the owner's rent**, even on houses where we keep the cleaning fee.

Simple example: a guest pays $80 rent (owner's) + $20 cleaning (ours), and Airbnb takes 10%, which is $10.
- **Fair:** the owner pays $8 (fee on their $80), and we pay $2 (fee on our $20).
- **What the rules do:** the owner pays all $10.

So owners pay a fee on money they never get. The line "add the cleaning fee only if it goes to the owner" decides who gets the cleaning *money*, but the fee always comes out of the owner's side no matter what.

In September this cost owners about **$44** in total (my estimate, splitting each fee in proportion to rent and cleaning). I followed the rule as written, because it's their stated policy, but it's something I'd ask the business about.

---

## 8. How I built it

I kept the code simple, with one job per file:

| File | Job |
|---|---|
| `loader.py` | Reads the CSV files |
| `payouts.py` | The owner math and statements |
| `reconcile.py` | Matches deposits to bookings and lists the problems |
| `cli.py` | Terminal version: prints everything and saves spreadsheets |
| `web/app.py` | Website version: overview, a page per owner, reconciliation page |

The terminal and website both use the same functions, so the numbers can't be different between them.

I chose **simple matching passes** instead of one clever algorithm on purpose. Each match type is easy to explain, and the report says *which* rule matched each deposit, so anyone reviewing it can follow along.

---

## 9. How I checked it was right

- **Hand calculations first.** I worked out every expected deposit before writing the program, and the program's results match mine exactly.
- **39 tests**, covering:
  - The money math, using real examples (because getting owner pay wrong is the worst possible mistake)
  - Each matching rule, using small made-up cases
  - A full run on the real September data, with all the totals locked in
- **I tried breaking it on purpose.** I changed the rounding rule, and a test failed like it should.

---

## 10. Final numbers

| Owner | Earned | Held | Pay now |
|---|---:|---:|---:|
| Dana Whitfield | $3,845.55 | $0.00 | $3,845.55 |
| Marcus Oyelaran | $4,691.94 | $0.00 | $4,691.94 |
| Priya Raman | $1,811.30 | $0.00 | $1,811.30 |
| Tom & Lisa Becker | $2,732.67 | $657.89 | $2,074.78 |
| **Total** | **$13,081.46** | **$657.89** | **$12,423.57** |

**Bank:** $22,632.15 came in. $20,630.15 matches bookings. The other **$2,002.00** is the duplicate ($1,552) plus the Zelle payment ($450).

---

## 11. What I'd do with more time

1. **Ask the business** which month a booking should count in, and about the cleaning-fee issue above.
2. **Use Airbnb's payout report**, which lists which bookings are in each payout. Then Airbnb matching wouldn't be a guess.
3. **Carry money forward between months**, so the Beckers' held $657.89 gets paid automatically once Vrbo pays.
4. **Let a person fix matches by hand** (e.g. "this Zelle payment is a damage fee for R1006") and have the program remember it.
5. **Upload files through the website** instead of replacing them in a folder.
6. **PDF statements** that can be emailed to owners.
