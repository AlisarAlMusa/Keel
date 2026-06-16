"""Async HTTP client for the model-server (intent + grad-risk).

Fail-safe contract
------------------
* ``predict_intent``   → returns ``None`` on any failure; caller routes to agent.
* ``predict_grad_risk``→ returns ``None`` on any failure; caller omits risk score.

Both methods retry once on transient network errors (connection reset, timeout)
before giving up.  Non-transient errors (4xx, bad JSON) are logged and swallowed
immediately — they should NOT trigger a retry.
"""

from __future__ import annotations

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from keel.domain.schemas import GradRiskPrediction, IntentPrediction
from keel.logging import get_logger

_log = get_logger(__name__)

_TRANSIENT: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
)
_RETRY: dict[str, object] = {
    "stop": stop_after_attempt(2),
    "wait": wait_exponential(multiplier=0.05, max=0.3),
    "retry": retry_if_exception_type(_TRANSIENT),
}


class ModelClient:
    """Thin async wrapper around the model-server HTTP API.

    Instantiate once during lifespan; store on ``app.state.model_client``.
    Call ``aclose()`` in the lifespan teardown.
    """

    def __init__(self, base_url: str, timeout: float = 2.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def predict_intent(self, text: str) -> IntentPrediction | None:
        """Call ``/intent``.  Returns ``None`` on failure — caller routes to agent."""
        try:
            async for attempt in AsyncRetrying(**_RETRY):  # type: ignore[arg-type]
                with attempt:
                    resp = await self._client.post("/intent", json={"text": text})
                    resp.raise_for_status()
                    data = resp.json()
                    return IntentPrediction(label=data["label"], confidence=data["confidence"])
        except Exception as exc:
            _log.warning("model_client.intent_failed", error=str(exc))
        return None

    async def predict_grad_risk(self, features: list[float]) -> GradRiskPrediction | None:
        """Call ``/grad_risk``.  Returns ``None`` on failure — caller omits risk score."""
        try:
            async for attempt in AsyncRetrying(**_RETRY):  # type: ignore[arg-type]
                with attempt:
                    resp = await self._client.post("/grad_risk", json={"features": features})
                    resp.raise_for_status()
                    data = resp.json()
                    return GradRiskPrediction(score=data["score"], label=data["label"])
        except Exception as exc:
            _log.warning("model_client.grad_risk_failed", error=str(exc))
        return None
