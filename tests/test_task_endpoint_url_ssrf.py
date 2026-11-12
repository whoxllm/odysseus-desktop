"""Scheduled tasks and the assistant may only target registered model endpoints.

The scheduler POSTs task prompts to ``endpoint_url`` server-side, and any
signed-in user can set it, so an unregistered URL (for example cloud metadata
at 169.254.169.254) must be rejected (CodeQL py/full-ssrf).
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, ModelEndpoint
import src.endpoint_resolver as endpoint_resolver


@pytest.fixture
def endpoints_db(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine)
    db = TestSessionLocal()
    db.add_all([
        ModelEndpoint(id="shared", name="Local vLLM", base_url="http://localhost:8002/v1", is_enabled=True),
        ModelEndpoint(id="bob", name="Bob's", base_url="http://10.0.0.9:8000/v1", is_enabled=True, owner="bob"),
        ModelEndpoint(id="off", name="Disabled", base_url="http://10.0.0.7:8000/v1", is_enabled=False),
    ])
    db.commit()
    db.close()
    monkeypatch.setattr(endpoint_resolver, "SessionLocal", TestSessionLocal)
    return TestSessionLocal


@pytest.mark.parametrize("url", [
    "http://localhost:8002/v1",
    "http://localhost:8002/v1/chat/completions",
    "http://localhost:8002/v1/",
])
def test_registered_endpoint_urls_are_accepted(endpoints_db, url):
    assert endpoint_resolver.is_registered_endpoint_url(url, "alice")


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",
    "http://localhost:8003/v1/chat/completions",
    "http://localhost:8002.evil.example/v1",
    "http://10.0.0.7:8000/v1/chat/completions",  # disabled
    "http://10.0.0.9:8000/v1/chat/completions",  # another user's endpoint
    "",
])
def test_unregistered_endpoint_urls_are_rejected(endpoints_db, url):
    assert not endpoint_resolver.is_registered_endpoint_url(url, "alice")


def test_owner_sees_own_endpoint(endpoints_db):
    assert endpoint_resolver.is_registered_endpoint_url("http://10.0.0.9:8000/v1/chat/completions", "bob")


def test_task_route_rejects_unregistered_endpoint(endpoints_db):
    from fastapi import HTTPException
    from routes.task.task_routes import _require_registered_endpoint

    _require_registered_endpoint(None, "alice")
    _require_registered_endpoint("", "alice")
    _require_registered_endpoint("http://localhost:8002/v1/chat/completions", "alice")
    with pytest.raises(HTTPException) as exc:
        _require_registered_endpoint("http://169.254.169.254/latest/meta-data/", "alice")
    assert exc.value.status_code == 400
