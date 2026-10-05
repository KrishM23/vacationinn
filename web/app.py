"""Small Flask UI on top of the same code the CLI uses.

    python web/app.py          then open http://127.0.0.1:5050
"""
from __future__ import annotations

import sys
from pathlib import Path

from flask import Flask, abort, render_template, request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from payouts.reconcile import CHANNEL_NAMES  # noqa: E402
from payouts.report import run_month  # noqa: E402

DATA_DIR = ROOT / "data"
DEFAULT_MONTH = "2026-09"

app = Flask(__name__)


@app.template_filter("money")
def money_filter(value):
    if value is None:
        return ""
    return f"${value:,.2f}" if value >= 0 else f"-${-value:,.2f}"


@app.template_filter("pct")
def pct_filter(value):
    return f"{(value * 100).normalize():f}%"   # Decimal('0.20') -> '20%'


@app.template_filter("channel")
def channel_filter(value):
    return CHANNEL_NAMES.get(value, value)


def load_report():
    month_text = request.args.get("month", DEFAULT_MONTH)
    try:
        year, month = (int(x) for x in month_text.split("-"))
    except ValueError:
        abort(400, "month must look like 2026-09")
    return run_month(DATA_DIR, year, month)


@app.context_processor
def add_globals():
    return {"month_param": request.args.get("month", DEFAULT_MONTH)}


@app.route("/")
def overview():
    report = load_report()
    return render_template("overview.html", report=report, page="overview")


@app.route("/owner/<slug>")
def owner(slug):
    report = load_report()
    statement = report.statements.statement_for(slug)
    if statement is None:
        abort(404)
    return render_template("owner.html", report=report, s=statement, page="owners",
                           status=report.reconciliation.reservation_status)


@app.route("/reconciliation")
def reconciliation():
    report = load_report()
    return render_template("reconciliation.html", report=report, r=report.reconciliation, page="recon")


if __name__ == "__main__":
    # not 5000: macOS AirPlay Receiver already listens there
    app.run(debug=True, port=5050)
