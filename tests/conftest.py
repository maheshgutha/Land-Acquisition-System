"""Test setup. Environment variables must be set before `app` is imported (the engine is created at import)."""
import os
import tempfile
import warnings

_tmp = tempfile.mkdtemp(prefix="landscan_test_")
os.environ["LANDSCAN_DB"] = f"sqlite:///{_tmp}/test.db"
os.environ["LANDSCAN_STORAGE"] = f"{_tmp}/storage"
os.environ["LANDSCAN_MODELS"] = f"{_tmp}/models"
os.environ["LANDSCAN_SECRET"] = "test-secret-key-that-is-long-enough-for-hs256"
os.environ["LANDSCAN_AUTOSEED"] = "1"
warnings.filterwarnings("ignore")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

PASSWORD = "demo1234"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:  # runs the lifespan: creates tables and seeds the demo data
        yield c


@pytest.fixture(scope="session")
def auth(client):
    cache: dict[str, dict] = {}

    def headers(username: str) -> dict:
        if username not in cache:
            r = client.post("/api/auth/login", data={"username": username, "password": PASSWORD})
            assert r.status_code == 200, r.text
            cache[username] = {"Authorization": f"Bearer {r.json()['access_token']}"}
        return cache[username]

    return headers
