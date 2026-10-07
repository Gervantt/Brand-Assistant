"""RBAC matrix: every role x every tool/action, spelled out explicitly."""

import pytest

from brand_shared.permissions import (
    Action,
    Role,
    Tool,
    can,
    can_use_tool,
    llm_tools_for,
)

QA = {Tool.SEARCH_BRANDBOOK, Tool.GET_CLIENT_PROFILE}
GEN = {Tool.CREATE_CONTENT_PLAN, Tool.WRITE_POST, Tool.CREATE_DESIGNER_BRIEF}

EXPECTED_LLM_TOOLS: dict[Role, set[Tool]] = {
    Role.VIEWER: QA,
    Role.COPYWRITER: QA | GEN,
    Role.MANAGER: QA | GEN | {Tool.PUBLISH_CONTENT_PLAN},
    Role.ADMIN: QA | GEN | {Tool.PUBLISH_CONTENT_PLAN},
}

EXPECTED_ACTIONS: dict[Role, set[Action]] = {
    Role.VIEWER: {Action.ASK},
    Role.COPYWRITER: {Action.ASK, Action.GENERATE, Action.UPLOAD_DOCUMENTS},
    Role.MANAGER: {Action.ASK, Action.GENERATE, Action.UPLOAD_DOCUMENTS, Action.PUBLISH_PLAN},
    Role.ADMIN: set(Action),
}


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("tool", [t for t in Tool if t is not Tool.INGEST_DOCUMENT])
def test_tool_matrix(role: Role, tool: Tool) -> None:
    assert can_use_tool(role, tool.value) is (tool in EXPECTED_LLM_TOOLS[role])


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("action", list(Action))
def test_action_matrix(role: Role, action: Action) -> None:
    assert can(role, action) is (action in EXPECTED_ACTIONS[role])


@pytest.mark.parametrize("role", list(Role))
def test_model_never_sees_internal_or_forbidden_tools(role: Role) -> None:
    visible = set(llm_tools_for(role))
    assert visible == EXPECTED_LLM_TOOLS[role]
    assert Tool.INGEST_DOCUMENT not in visible


@pytest.mark.parametrize("name", ["delete_everything", "", "PUBLISH_CONTENT_PLAN", "publish"])
def test_unknown_tool_names_are_denied_even_for_admin(name: str) -> None:
    assert not can_use_tool(Role.ADMIN, name)


def test_ingest_requires_upload_permission() -> None:
    assert not can_use_tool(Role.VIEWER, Tool.INGEST_DOCUMENT.value)
    assert can_use_tool(Role.COPYWRITER, Tool.INGEST_DOCUMENT.value)
