"""Tests of confirming AI duration estimates and of each estimate's source.

Purpose: AI estimates count only once a person confirms them; a duration a person types in is
theirs. Prove confirming works for all or chosen nodes and the state is derived correctly.
How it fits: Exercises app/api/v1/wbs.py with the fake WBS repository and the pure `estimate_state`
(app/domain/wbs.py); layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

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


def _ai_node(
    wbs_data: WbsStore, project_id: str, parent: str, name: str, days: int | None
) -> uuid.UUID:
    """Add an AI-made work package with an unconfirmed estimate under `parent`."""
    node_id = uuid.uuid4()
    wbs_data.seq += 1
    wbs_data.nodes[node_id] = {
        "id": node_id,
        "project_id": uuid.UUID(project_id),
        "parent_id": uuid.UUID(parent),
        "level": 3,
        "sort_order": wbs_data.seq,
        "name": name,
        "description": None,
        "duration_days": days,
        "effort_hours": None,
        "owner_employee_id": None,
        "origin": "ai",
        "scope_item_id": None,
        "seq": wbs_data.seq,
    }
    return node_id


def test_confirm_estimates(
    wbs_client: TestClient, store: Store, wbs_store: WbsStore, auth: Auth
) -> None:
    """Typed durations are the PM's; confirming marks chosen (or all) AI estimates as confirmed."""
    user, org, project_id = create_project(wbs_client, store, auth)
    headers = auth(user)
    root = add_node(wbs_client, project_id, headers, name="CRM")
    deliverable = add_node(wbs_client, project_id, headers, parentId=root["id"], name="Build")
    first = _ai_node(wbs_store, project_id, deliverable["id"], "API", 10)
    second = _ai_node(wbs_store, project_id, deliverable["id"], "UI", 15)
    _ai_node(wbs_store, project_id, deliverable["id"], "Docs", None)
    mine = add_node(
        wbs_client, project_id, headers, parentId=deliverable["id"], name="QA", durationDays=5
    )
    assert (mine["estimateConfirmed"], mine["estimateSource"]) == (True, "yours")

    def states() -> dict[str, tuple[bool, str | None]]:
        """Return each node's (confirmed, source) by name, as the tree shows it."""
        tree = wbs_client.get(f"/api/v1/projects/{project_id}/wbs", headers=headers).json()
        return {node["name"]: (node["estimateConfirmed"], node["estimateSource"]) for node in tree}

    assert states()["API"] == (False, "ai")
    url = f"/api/v1/projects/{project_id}/wbs/confirm-estimates"
    response = wbs_client.post(url, json={"nodeIds": [str(first)]}, headers=headers)
    assert response.status_code == 200, response.text
    assert (states()["API"], states()["UI"]) == ((True, "ai"), (False, "ai"))

    # Typing a new duration confirms it; clearing it leaves nothing to confirm.
    wbs_client.patch(f"/api/v1/wbs/nodes/{second}", json={"durationDays": 20}, headers=headers)
    assert states()["UI"] == (True, "ai")
    assert wbs_client.post(url, headers=headers).status_code == 200
    assert states()["Docs"] == (False, None)

    viewer = store.add_user("Vic")
    store.members[(org, viewer)] = OrgRole.VIEWER
    assert wbs_client.post(url, json={}, headers=auth(viewer)).status_code == 403
    missing = f"/api/v1/projects/{uuid.uuid4()}/wbs/confirm-estimates"
    assert wbs_client.post(missing, json={}, headers=headers).status_code == 404


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
    """AI estimates count once confirmed; typed durations are the PM's; none means no source."""
    assert estimate_state(origin, days, confirmed_at) == expected
