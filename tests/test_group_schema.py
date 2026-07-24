import pytest

from app.schemas.group import AddGroupMemberRequest


def test_add_group_member_request_strips_name():
    request = AddGroupMemberRequest(name="  Jane Doe  ", email="jane@university.edu")

    assert request.name == "Jane Doe"


def test_add_group_member_request_normalizes_email():
    request = AddGroupMemberRequest(name="Jane Doe", email="Jane@University.EDU")

    assert request.email == "jane@university.edu"

