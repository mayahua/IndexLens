"""Tests for configurable provider deadlines and zero-retry timeout handling."""

import asyncio
from typing import Any

import httpx
import pytest

from indexlens.api import openai_provider
from indexlens.api.explanation import ExplainRequest
from indexlens.api.openai_provider import ProviderSettings
from indexlens.api.service import ApiError
from tests.test_explanations import MockProvider, evidence


@pytest.mark.parametrize(
    "configured,expected", [(None, 60), ("30", 30), ("45", 45), ("180", 180)]
)
def test_provider_deadline_configuration(
    monkeypatch: pytest.MonkeyPatch,
    configured: str | None,
    expected: float,
) -> None:
    monkeypatch.delenv("INDEXLENS_OPENAI_TIMEOUT_SECONDS", raising=False)
    if configured is not None:
        monkeypatch.setenv("INDEXLENS_OPENAI_TIMEOUT_SECONDS", configured)
    assert ProviderSettings.from_env().timeout_seconds == expected


@pytest.mark.parametrize("value", ["0", "-1", "180.1", "nan", "inf", "-inf", "invalid"])
def test_invalid_deadline_fails_before_provider_access(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    mock = MockProvider()
    monkeypatch.setattr(openai_provider, "AsyncOpenAI", mock.client)
    monkeypatch.setenv("OPENAI_API_KEY", "mock-never-live")
    monkeypatch.setenv("INDEXLENS_OPENAI_TIMEOUT_SECONDS", value)
    with pytest.raises(ApiError) as error:
        asyncio.run(
            openai_provider.explain(
                ExplainRequest.model_validate(
                    {"target": {"kind": "run", "run_id": "sample"}}
                ),
                evidence(),
            )
        )
    assert error.value.code == "openai_configuration"
    assert mock.options == mock.requests == []


@pytest.mark.parametrize("configured,expected", [(None, 60), ("180", 180)])
@pytest.mark.parametrize("failure", [False, True])
def test_deadline_reaches_sdk_and_records_success_or_timeout(
    monkeypatch: pytest.MonkeyPatch,
    failure: bool,
    configured: str | None,
    expected: float,
) -> None:
    mock = MockProvider()
    monkeypatch.setattr(openai_provider, "AsyncOpenAI", mock.client)
    monkeypatch.setenv("OPENAI_API_KEY", "mock-never-live")
    monkeypatch.delenv("INDEXLENS_OPENAI_TIMEOUT_SECONDS", raising=False)
    if configured is not None:
        monkeypatch.setenv("INDEXLENS_OPENAI_TIMEOUT_SECONDS", configured)
    monkeypatch.setenv("INDEXLENS_OPENAI_RETRIES", "0")
    bundle = evidence()
    bundle["interpretation"] = dict(boundary="Limited", data_label="synthetic")
    if failure:

        def timeout(body: dict[str, Any]) -> httpx.Response:
            raise httpx.ReadTimeout("private transport details")

        mock.handler = timeout
    operation = openai_provider.explain(
        ExplainRequest.model_validate({"target": {"kind": "run", "run_id": "sample"}}),
        bundle,
    )
    if failure:
        with pytest.raises(ApiError) as error:
            asyncio.run(operation)
        assert error.value.status == 504
        assert error.value.code == "openai_timeout"
        assert "private" not in str(error.value)
    else:
        result = asyncio.run(operation)
        assert result["generation"]["timeout_seconds"] == expected
        assert result["generation"]["retries"] == 0
    assert len(mock.requests) == 1
    assert mock.options[0]["timeout"] == expected
    assert mock.options[0]["max_retries"] == 0


def test_extended_total_deadline_cancels_and_closes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[float | None] = []
    events: list[str] = []
    actual_timeout = asyncio.timeout

    def accelerated_timeout(delay: float | None) -> asyncio.Timeout:
        seen.append(delay)
        return actual_timeout(0.01)

    class SlowResponses:
        async def parse(self, **kwargs: Any) -> None:
            events.append("attempt")
            try:
                await asyncio.sleep(10)
            finally:
                events.append("cancelled")

    class SlowClient:
        responses = SlowResponses()

        async def __aenter__(self) -> "SlowClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            events.append("closed")

    monkeypatch.setattr(openai_provider, "AsyncOpenAI", lambda **kw: SlowClient())
    monkeypatch.setattr(asyncio, "timeout", accelerated_timeout)
    monkeypatch.setenv("OPENAI_API_KEY", "mock-never-live")
    monkeypatch.setenv("INDEXLENS_OPENAI_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("INDEXLENS_OPENAI_RETRIES", "0")
    with pytest.raises(ApiError) as error:
        asyncio.run(
            openai_provider.explain(
                ExplainRequest.model_validate(
                    {"target": {"kind": "run", "run_id": "sample"}}
                ),
                evidence(),
            )
        )
    assert error.value.code == "openai_timeout"
    assert seen == [180]
    assert events == ["attempt", "cancelled", "closed"]
