from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.core.security import (
    AccessTokenError,
    AccessTokenService,
    PasswordService,
    SecretHasher,
    normalize_email,
    normalize_phone,
)
from app.modules.audit import redact_audit_changes
from app.modules.identity.schemas import RegisterRequest


def test_identity_normalization_is_canonical_and_strict() -> None:
    assert normalize_email("  USER@ExAmPle.COM. ") == "user@example.com"
    assert normalize_phone(" +919876543210 ") == "+919876543210"

    with pytest.raises(ValueError):
        normalize_email("not-an-email")
    with pytest.raises(ValueError):
        normalize_phone("9876543210")


def test_registration_forbids_account_type_selection() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(
            {
                "email": "user@example.com",
                "password": "correct horse battery staple",
                "account_type": "seller",
            }
        )


def test_argon2id_hashing_and_verification() -> None:
    passwords = PasswordService()
    encoded = passwords.hash("correct horse battery staple", normalized_email="person@example.com")

    assert encoded.startswith("$argon2id$")
    assert passwords.verify(encoded, "correct horse battery staple")
    assert not passwords.verify(encoded, "incorrect password")
    assert "correct horse battery staple" not in encoded


def test_password_policy_rejects_short_common_and_identity_passwords() -> None:
    passwords = PasswordService()

    with pytest.raises(ValueError):
        passwords.hash("short")
    with pytest.raises(ValueError):
        passwords.hash("password1234")
    with pytest.raises(ValueError):
        passwords.hash("safvan-secure-value", normalized_email="safvan@example.com")


def test_secret_hashing_is_keyed_and_timing_safe_api_matches() -> None:
    first = SecretHasher("a" * 32)
    second = SecretHasher("b" * 32)

    digest = first.digest("opaque-secret")
    assert first.verify("opaque-secret", digest)
    assert not first.verify("wrong-secret", digest)
    assert second.digest("opaque-secret") != digest
    assert "opaque-secret" not in digest


def test_audit_redaction_removes_nested_secrets_and_pii() -> None:
    redacted = redact_audit_changes(
        {
            "status": "active",
            "password_hash": "private",
            "nested": {"refresh_token": "private", "role": "admin"},
            "email": "private@example.com",
        }
    )

    assert redacted == {
        "status": "active",
        "password_hash": "[REDACTED]",
        "nested": {"refresh_token": "[REDACTED]", "role": "admin"},
        "email": "[REDACTED]",
    }


def test_access_tokens_require_fixed_algorithm_issuer_audience_and_expiry() -> None:
    tokens = AccessTokenService(
        signing_key="test-access-signing-key-which-is-long-enough",
        issuer="wheelmatch-test",
        audience="wheelmatch-client",
        ttl_seconds=900,
    )
    user_id = UUID("018f0000-0000-7000-8000-000000000001")
    family_id = UUID("018f0000-0000-7000-8000-000000000002")
    now = datetime.now(UTC)

    encoded = tokens.issue(user_id=user_id, session_family_id=family_id, now=now)
    claims = tokens.decode(encoded)

    assert claims.user_id == user_id
    assert claims.session_family_id == family_id
    assert claims.expires_at == now.replace(microsecond=0) + timedelta(seconds=900)

    wrong_audience = AccessTokenService(
        signing_key="test-access-signing-key-which-is-long-enough",
        issuer="wheelmatch-test",
        audience="different-client",
        ttl_seconds=900,
    )
    with pytest.raises(AccessTokenError):
        wrong_audience.decode(encoded)


def test_expired_access_token_is_rejected() -> None:
    tokens = AccessTokenService(
        signing_key="test-access-signing-key-which-is-long-enough",
        issuer="wheelmatch-test",
        audience="wheelmatch-client",
        ttl_seconds=300,
    )
    encoded = tokens.issue(
        user_id=UUID("018f0000-0000-7000-8000-000000000001"),
        session_family_id=UUID("018f0000-0000-7000-8000-000000000002"),
        now=datetime.now(UTC) - timedelta(hours=1),
    )

    with pytest.raises(AccessTokenError):
        tokens.decode(encoded)
