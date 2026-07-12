from datetime import datetime, timezone
from types import SimpleNamespace

from app.models import AccountStatus, GroupMemberRole
from app.services.groups import serialize_members


def _membership(user_id: str, name: str, email: str, *, joined_at: datetime):
    user = SimpleNamespace(
        id=user_id,
        name=name,
        email=email,
        account_status=AccountStatus.ACTIVE,
    )
    return SimpleNamespace(
        user_id=user_id,
        user=user,
        role=GroupMemberRole.STUDENT,
        joined_at=joined_at,
    )


def test_serialize_members_marks_owner_and_sorts_by_joined_at():
    group = SimpleNamespace(owner_id="u1")
    earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
    later = datetime(2026, 1, 2, tzinfo=timezone.utc)
    alice = _membership("u1", "Alice", "alice@example.com", joined_at=later)
    bob = _membership("u2", "Bob", "bob@example.com", joined_at=earlier)

    members = serialize_members(group, [alice, bob])

    assert [member.name for member in members] == ["Bob", "Alice"]
    assert members[0].is_owner is False
    assert members[1].is_owner is True
