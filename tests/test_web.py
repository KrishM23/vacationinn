"""Smoke test that every page renders."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))

from app import app  # noqa: E402


def test_pages_render():
    client = app.test_client()
    for url in ["/", "/owner/tom-lisa-becker", "/reconciliation", "/?month=2026-08"]:
        resp = client.get(url)
        assert resp.status_code == 200, url
    assert b"$2,074.78" in client.get("/owner/tom-lisa-becker").data


def test_unknown_owner_404():
    assert app.test_client().get("/owner/nobody").status_code == 404
