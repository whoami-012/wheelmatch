from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.core.events import EventEnvelope
from app.core.ids import uuid7


def test_uuid7_has_expected_version_and_sortable_timestamp() -> None:
    identifiers = [uuid7() for _ in range(20)]

    assert all(identifier.version == 7 for identifier in identifiers)
    timestamps = [identifier.int >> 80 for identifier in identifiers]
    assert timestamps == sorted(timestamps)


def test_event_envelope_is_strict_and_uses_utc() -> None:
    envelope = EventEnvelope(
        event_type="system.smoke_test",
        aggregate_type="system",
        aggregate_id=UUID("018f0000-0000-7000-8000-000000000001"),
        occurred_at=datetime.now(UTC),
        payload={"synthetic": True},
    )

    assert envelope.schema_version == 1
    assert envelope.occurred_at.tzinfo is UTC


def test_event_envelope_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(
            {
                "event_type": "system.smoke_test",
                "aggregate_type": "system",
                "aggregate_id": "018f0000-0000-7000-8000-000000000001",
                "occurred_at": datetime.now(UTC).isoformat(),
                "payload": {},
                "secret": "must-not-pass",
            }
        )
