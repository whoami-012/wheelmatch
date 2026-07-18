from __future__ import annotations

import re
import unicodedata

_EMAIL_LOCAL_RE = re.compile(r"^[^\s@]{1,64}$")
_PHONE_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")


def normalize_email(value: str) -> str:
    candidate = unicodedata.normalize("NFKC", value).strip()
    if candidate.count("@") != 1:
        raise ValueError("invalid email address")
    local, domain = candidate.rsplit("@", 1)
    if not _EMAIL_LOCAL_RE.fullmatch(local) or not domain or len(candidate) > 320:
        raise ValueError("invalid email address")
    try:
        ascii_domain = domain.rstrip(".").encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("invalid email address") from exc
    if (
        not ascii_domain
        or len(ascii_domain) > 255
        or "." not in ascii_domain
        or any(not label or len(label) > 63 for label in ascii_domain.split("."))
    ):
        raise ValueError("invalid email address")
    return f"{local.casefold()}@{ascii_domain.casefold()}"


def normalize_phone(value: str) -> str:
    candidate = unicodedata.normalize("NFKC", value).strip()
    if not _PHONE_RE.fullmatch(candidate):
        raise ValueError("phone must be in E.164 format")
    return candidate
