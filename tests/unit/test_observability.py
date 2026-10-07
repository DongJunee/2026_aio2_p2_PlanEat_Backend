import os

from app.core.config import Settings
from app.core.observability import build_langsmith_run_config


def test_langsmith_is_disabled_without_explicit_configuration() -> None:
    settings = Settings(langsmith_tracing=True, langsmith_api_key=None)

    assert (
        build_langsmith_run_config(
            settings,
            session_id="session-001",
            workflow_step="WAITING_IMAGE",
            has_image=False,
            has_conditions=False,
            has_confirmed_ingredients=False,
            attachment_count=0,
        )
        is None
    )


def test_langsmith_config_contains_only_non_sensitive_metadata(monkeypatch) -> None:
    class FakeTracer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    monkeypatch.setattr("app.core.observability.LangChainTracer", FakeTracer)
    for name in (
        "LANGSMITH_API_KEY",
        "LANGSMITH_TRACING",
        "LANGSMITH_HIDE_INPUTS",
        "LANGSMITH_HIDE_OUTPUTS",
        "LANGSMITH_PROJECT",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(
        langsmith_tracing=True,
        langsmith_api_key="test-langsmith-key",
        langsmith_project="planeat-test",
    )

    config = build_langsmith_run_config(
        settings,
        session_id="session-001",
        workflow_step="WAITING_IMAGE",
        has_image=True,
        has_conditions=False,
        has_confirmed_ingredients=False,
        attachment_count=1,
    )

    assert config is not None
    assert config["run_name"] == "planeat.chat"
    assert config["metadata"] == {
        "session_id_hash": "4e9bb9f661912a46",
        "workflow_step": "WAITING_IMAGE",
        "has_image": True,
        "has_conditions": False,
        "has_confirmed_ingredients": False,
        "attachment_count": 1,
    }
    assert "session-001" not in str(config["metadata"])
    assert "LANGSMITH_API_KEY" not in os.environ
    assert "LANGSMITH_TRACING" not in os.environ
