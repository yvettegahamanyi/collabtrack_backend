import uuid

from app.models import IntegrationProvider, IntegrationProviderType, generate_uuid


def test_generate_uuid_returns_string_uuid():
    value = generate_uuid()

    parsed = uuid.UUID(value)
    assert str(parsed) == value


def test_integration_provider_type_decorator_roundtrip():
    decorator = IntegrationProviderType()

    assert decorator.process_bind_param(IntegrationProvider.github, None) == "github"
    assert decorator.process_bind_param("github", None) == "github"
    assert decorator.process_bind_param(None, None) is None
    assert decorator.process_result_value("google", None) == IntegrationProvider.google
    assert decorator.process_result_value(None, None) is None
