"""Single source of truth for role-based permissions (RBAC).

Used by the API gateway (endpoint guards, tool filtering before the model sees tools, per-call
re-checks) and by the MCP server (defense in depth). Client-level access (ABAC) is separate:
a user may act only on clients listed in their `client_ids`; admins may act on all clients.
"""

from enum import StrEnum


class Role(StrEnum):
    VIEWER = "viewer"
    COPYWRITER = "copywriter"
    MANAGER = "manager"
    ADMIN = "admin"


class Action(StrEnum):
    ASK = "ask"  # brand-book Q&A, read client profile
    GENERATE = "generate"  # content plans, posts, designer briefs
    UPLOAD_DOCUMENTS = "upload_documents"
    PUBLISH_PLAN = "publish_plan"
    MANAGE_CLIENTS = "manage_clients"
    MANAGE_USERS = "manage_users"
    VIEW_METRICS = "view_metrics"


# Roles are cumulative: each role has everything the previous one has.
ROLE_ORDER: tuple[Role, ...] = (Role.VIEWER, Role.COPYWRITER, Role.MANAGER, Role.ADMIN)

_GRANTS: dict[Role, frozenset[Action]] = {
    Role.VIEWER: frozenset({Action.ASK}),
    Role.COPYWRITER: frozenset({Action.GENERATE, Action.UPLOAD_DOCUMENTS}),
    Role.MANAGER: frozenset({Action.PUBLISH_PLAN}),
    Role.ADMIN: frozenset({Action.MANAGE_CLIENTS, Action.MANAGE_USERS, Action.VIEW_METRICS}),
}


def _cumulative() -> dict[Role, frozenset[Action]]:
    result: dict[Role, frozenset[Action]] = {}
    acc: frozenset[Action] = frozenset()
    for role in ROLE_ORDER:
        acc = acc | _GRANTS[role]
        result[role] = acc
    return result


ROLE_ACTIONS: dict[Role, frozenset[Action]] = _cumulative()


class Tool(StrEnum):
    SEARCH_BRANDBOOK = "search_brandbook"
    GET_CLIENT_PROFILE = "get_client_profile"
    CREATE_CONTENT_PLAN = "create_content_plan"
    WRITE_POST = "write_post"
    CREATE_DESIGNER_BRIEF = "create_designer_brief"
    PUBLISH_CONTENT_PLAN = "publish_content_plan"
    # Internal: called by the gateway's upload endpoint, never offered to the model.
    INGEST_DOCUMENT = "ingest_document"


TOOL_ACTION: dict[Tool, Action] = {
    Tool.SEARCH_BRANDBOOK: Action.ASK,
    Tool.GET_CLIENT_PROFILE: Action.ASK,
    Tool.CREATE_CONTENT_PLAN: Action.GENERATE,
    Tool.WRITE_POST: Action.GENERATE,
    Tool.CREATE_DESIGNER_BRIEF: Action.GENERATE,
    Tool.PUBLISH_CONTENT_PLAN: Action.PUBLISH_PLAN,
    Tool.INGEST_DOCUMENT: Action.UPLOAD_DOCUMENTS,
}

INTERNAL_TOOLS: frozenset[Tool] = frozenset({Tool.INGEST_DOCUMENT})


def can(role: Role, action: Action) -> bool:
    return action in ROLE_ACTIONS[role]


def can_use_tool(role: Role, tool_name: str) -> bool:
    """Unknown tool names are denied (a model may hallucinate or be prompt-injected)."""
    try:
        tool = Tool(tool_name)
    except ValueError:
        return False
    return can(role, TOOL_ACTION[tool])


def llm_tools_for(role: Role) -> list[Tool]:
    """Tools the model may see for this role, in declaration order."""
    return [t for t in Tool if t not in INTERNAL_TOOLS and can(role, TOOL_ACTION[t])]
