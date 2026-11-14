"""Moved test support."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import CurrentPrincipal
from tests.support.store import Store
from tests.support.wbs import FakeWbs, WbsStore


@pytest.fixture
def wbs_store() -> WbsStore:
    return WbsStore()


@pytest.fixture
def wbs_client(app: FastAPI, store: Store, wbs_store: WbsStore) -> Iterator[TestClient]:
    def repo(principal: CurrentPrincipal) -> FakeWbs:
        return FakeWbs(store, wbs_store, principal.user_id)

    app.dependency_overrides[deps.get_wbs_repo] = repo
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
