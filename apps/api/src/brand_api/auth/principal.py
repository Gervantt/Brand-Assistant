import uuid
from dataclasses import dataclass

from brand_shared.db.models import User
from brand_shared.permissions import Action, Role, can, can_use_tool


@dataclass(frozen=True)
class Principal:
    """The authenticated caller: role for RBAC, client_ids for ABAC."""

    id: uuid.UUID
    email: str
    role: Role
    client_ids: frozenset[uuid.UUID]
    is_demo: bool = False

    @classmethod
    def from_user(cls, user: User) -> "Principal":
        return cls(
            id=user.id,
            email=user.email,
            role=Role(user.role),
            client_ids=frozenset(client.id for client in user.clients),
            is_demo=user.is_demo,
        )

    def can(self, action: Action) -> bool:
        return can(self.role, action)

    def can_use_tool(self, tool_name: str) -> bool:
        return can_use_tool(self.role, tool_name)

    def can_access_client(self, client_id: uuid.UUID) -> bool:
        return self.role is Role.ADMIN or client_id in self.client_ids
