from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class VehicleIdentityInvalid(ValueError):
    pass


class VehicleIdentityNormalizerUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class NormalizedVehicleIdentity:
    jurisdiction: str
    registration: str
    vin: str | None
    chassis: str | None


@dataclass(frozen=True, slots=True)
class KeyedVehicleIdentity:
    jurisdiction: str
    registration_hmac: str
    vin_hmac: str | None
    chassis_hmac: str | None
    hash_version: int


class VehicleIdentityNormalizer(Protocol):
    identifier: str

    def normalize(
        self,
        *,
        jurisdiction: str,
        registration: str,
        vin: str | None,
        chassis: str | None,
    ) -> NormalizedVehicleIdentity: ...


class DisabledVehicleIdentityNormalizer:
    identifier = "disabled"

    def normalize(
        self,
        *,
        jurisdiction: str,
        registration: str,
        vin: str | None,
        chassis: str | None,
    ) -> NormalizedVehicleIdentity:
        del jurisdiction, registration, vin, chassis
        raise VehicleIdentityNormalizerUnavailable


class DeterministicVehicleIdentityNormalizer:
    """Strict local/test normalizer; it performs no external lookups."""

    identifier = "deterministic"
    _JURISDICTION = re.compile(r"^[A-Z]{2}(?:-[A-Z0-9]{1,8})?$")
    _ALNUM = re.compile(r"^[A-Z0-9]+$")
    _VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

    def normalize(
        self,
        *,
        jurisdiction: str,
        registration: str,
        vin: str | None,
        chassis: str | None,
    ) -> NormalizedVehicleIdentity:
        normalized_jurisdiction = jurisdiction.strip().upper()
        if not self._JURISDICTION.fullmatch(normalized_jurisdiction):
            raise VehicleIdentityInvalid("jurisdiction is invalid")
        normalized_registration = self._identifier(registration, minimum=4, maximum=20)
        normalized_vin = None if vin is None else self._identifier(vin, minimum=17, maximum=17)
        normalized_chassis = (
            None if chassis is None else self._identifier(chassis, minimum=6, maximum=32)
        )
        if normalized_vin is not None and not self._VIN.fullmatch(normalized_vin):
            raise VehicleIdentityInvalid("vin is invalid")
        if normalized_vin is None and normalized_chassis is None:
            raise VehicleIdentityInvalid("vin or chassis is required")
        return NormalizedVehicleIdentity(
            jurisdiction=normalized_jurisdiction,
            registration=normalized_registration,
            vin=normalized_vin,
            chassis=normalized_chassis,
        )

    @classmethod
    def _identifier(cls, value: str, *, minimum: int, maximum: int) -> str:
        normalized = value.strip().upper().replace(" ", "").replace("-", "")
        if not minimum <= len(normalized) <= maximum or not cls._ALNUM.fullmatch(normalized):
            raise VehicleIdentityInvalid("vehicle identifier is invalid")
        return normalized


def key_vehicle_identity(
    identity: NormalizedVehicleIdentity, *, key: bytes, hash_version: int
) -> KeyedVehicleIdentity:
    if not key or hash_version <= 0:
        raise ValueError("key and positive hash version are required")

    def digest(field: str, value: str) -> str:
        message = f"wheelmatch:vehicle-identity:v{hash_version}:{field}:{value}".encode()
        return hmac.new(key, message, hashlib.sha256).hexdigest()

    return KeyedVehicleIdentity(
        jurisdiction=identity.jurisdiction,
        registration_hmac=digest(
            "registration", f"{identity.jurisdiction}:{identity.registration}"
        ),
        vin_hmac=digest("vin", identity.vin) if identity.vin else None,
        chassis_hmac=digest("chassis", identity.chassis) if identity.chassis else None,
        hash_version=hash_version,
    )


def ownership_material_fingerprint(
    *,
    key: bytes,
    canonical_vehicle_id: UUID,
    canonical_identity_version: int,
    owner_user_id: UUID,
    identity_attempt_id: UUID,
    identity_projection_version: int,
    jurisdiction: str,
    ownership_basis: str,
    registration_hmac: str,
    vin_hmac: str | None,
    chassis_hmac: str | None,
    provider_result_version: int,
    provider_material_attributes: Mapping[str, str],
) -> str:
    material = {
        "canonical_vehicle_id": str(canonical_vehicle_id),
        "canonical_identity_version": canonical_identity_version,
        "owner_user_id": str(owner_user_id),
        "identity_attempt_id": str(identity_attempt_id),
        "identity_projection_version": identity_projection_version,
        "jurisdiction": jurisdiction,
        "ownership_basis": ownership_basis,
        "registration_hmac": registration_hmac,
        "vin_hmac": vin_hmac,
        "chassis_hmac": chassis_hmac,
        "provider_result_version": provider_result_version,
        "provider_material_attributes": dict(sorted(provider_material_attributes.items())),
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key, b"wheelmatch:ownership-material:" + encoded, hashlib.sha256).hexdigest()
