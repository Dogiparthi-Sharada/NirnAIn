"""Moved test support."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.domain.common import OrgRole
from app.domain.wbs import EstimateSource, estimate_state
from tests.support.projects import create_project
from tests.support.store import Store
from tests.support.tokens import Auth
from tests.support.wbs import WbsStore, add_node


def _ai_node(w: WbsStore, pid: str, parent: str, name: str, days: int | None) -> uuid.UUID:
    node_id = uuid.uuid4()
    w.seq += 1
    w.nodes[node_id] = {
        "id": node_id,
        "project_id": uuid.UUID(pid),
        "parent_id": uuid.UUID(parent),
        "level": 3,
        "sort_order": w.seq,
        "name": name,
        "description": None,
        "duration_days": days,
        "effort_hours": None,
        "owner_employee_id": None,
        "origin": "ai",
        "scope_item_id": None,
        "seq": w.seq,
    }
    return node_id


def test_confirm_estimates(
    wbs_client: TestClient, store: Store, wbs_store: WbsStore, auth: Auth
) -> None:
    user, org, pid = create_project(wbs_client, store, auth)
    h = auth(user)
    root = add_node(wbs_client, pid, h, name="CRM")
    deliverable = add_node(wbs_client, pid, h, parentId=root["id"], name="Build")
    first = _ai_node(wbs_store, pid, deliverable["id"], "API", 10)
    second = _ai_node(wbs_store, pid, deliverable["id"], "UI", 15)
    _ai_node(wbs_store, pid, deliverable["id"], "Docs", None)
    mine = add_node(wbs_client, pid, h, parentId=deliverable["id"], name="QA", durationDays=5)
    assert (mine["estimateConfirmed"], mine["estimateSource"]) == (True, "yours")

    def states() -> dict[str, tuple[bool, str | None]]:
        tree = wbs_client.get(f"/api/v1/projects/{pid}/wbs", headers=h).json()
        return {n["name"]: (n["estimateConfirmed"], n["estimateSource"]) for n in tree}

    assert states()["API"] == (False, "ai")
    url = f"/api/v1/projects/{pid}/wbs/confirm-estimates"
    r = wbs_client.post(url, json={"nodeIds": [str(first)]}, headers=h)
    assert r.status_code == 200, r.text
    assert (states()["API"], states()["UI"]) == ((True, "ai"), (False, "ai"))

    # Typing a new duration confirms it; clearing it leaves nothing to confirm.
    wbs_client.patch(f"/api/v1/wbs/nodes/{second}", json={"durationDays": 20}, headers=h)
    assert states()["UI"] == (True, "ai")
    assert wbs_client.post(url, headers=h).status_code == 200
    assert states()["Docs"] == (False, None)

    viewer = store.add_user("Vic")
    store.members[(org, viewer)] = OrgRole.VIEWER
    assert wbs_client.post(url, json={}, headers=auth(viewer)).status_code == 403
    missing = f"/api/v1/projects/{uuid.uuid4()}/wbs/confirm-estimates"
    assert wbs_client.post(missing, json={}, headers=h).status_code == 404


@pytest.mark.parametrize(
    ("origin", "days", "confirmed_at", "expected"),
    [
        ("ai", 10, None, (False, EstimateSource.AI)),
        ("ai", 10, datetime(2026, 11, 13, tzinfo=UTC), (True, EstimateSource.AI)),
        ("manual", 10, None, (True, EstimateSource.YOURS)),
        ("manual", None, None, (False, None)),
        ("ai", None, None, (False, None)),
    ],
)
def test_estimate_state(
    origin: str,
    days: int | None,
    confirmed_at: datetime | None,
    expected: tuple[bool, EstimateSource | None],
) -> None:
    assert estimate_state(origin, days, confirmed_at) == expected
