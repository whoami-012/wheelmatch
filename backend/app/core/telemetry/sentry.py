from __future__ import annotations

import sentry_sdk
from sentry_sdk.types import Event, Hint

from app.core.config import Settings

_SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key"}


def configure_sentry(settings: Settings) -> None:
    if settings.sentry_dsn is None or not settings.sentry_dsn.get_secret_value():
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        environment=settings.environment.value,
        release=settings.service_version,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        before_send=_before_send,
    )


def _before_send(event: Event, _hint: Hint) -> Event:
    request = event.get("request")
    if isinstance(request, dict):
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                key: "[Filtered]" if key.lower() in _SENSITIVE_HEADERS else value
                for key, value in headers.items()
            }
        request.pop("cookies", None)
        request.pop("data", None)
        request.pop("query_string", None)
    return event
