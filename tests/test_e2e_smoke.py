"""Optional end-to-end UI smoke test, driven by Playwright against a real, live server.

Every other test in this suite exercises the API directly (FastAPI's TestClient) and needs
nothing extra installed. This one drives an actual browser against the actual served HTML/JS/CSS,
so it catches a class of bug the API tests structurally cannot: a broken selector, a JS console
error, a form that doesn't wire up, a route that 404s from the browser's point of view.

It skips cleanly - not a failure - when Playwright or its Chromium binary isn't installed, so the
main `pytest -q` (36+ tests) never depends on it and always stays green with just requirements.txt.
To actually run this one:

    pip install -r requirements-dev.txt
    playwright install chromium
    pytest tests/test_e2e_smoke.py -q
"""
import socket
import threading
import time

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed - see this file's docstring")
from playwright.sync_api import sync_playwright  # noqa: E402

import uvicorn  # noqa: E402

from app.main import app  # noqa: E402

PASSWORD = "demo1234"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(client):  # depends on conftest's `client` fixture so the demo DB is seeded first
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not getattr(server, "started", False) and time.time() < deadline:
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True)
        except Exception as e:
            pytest.skip(f"Chromium not available for Playwright ({e}); run: playwright install chromium")
            return
        yield b
        b.close()


def test_login_view_dashboard_and_open_a_project(live_server, browser):
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(live_server, wait_until="networkidle")
    page.fill("#lu", "central")
    page.fill("#lp", PASSWORD)
    page.click('form[data-form="login"] button[type="submit"]')
    page.wait_for_selector("#app:not(.hidden)", timeout=8000)

    page.evaluate("location.hash = '#/dashboard'")
    page.wait_for_selector("text=National overview", timeout=8000)
    assert page.locator(".card").count() > 5, "dashboard should render its KPI cards"

    page.evaluate("location.hash = '#/projects'")
    page.wait_for_selector("table tbody tr", timeout=8000)
    page.locator("table tbody tr").first.click()
    page.wait_for_selector(".drawer .stepper", timeout=8000)
    assert page.locator(".drawer h3").count() == 1, "opening a project should show its drawer with a lifecycle stepper"

    assert not errors, f"JS errors during the walkthrough: {errors}"
    page.close()
