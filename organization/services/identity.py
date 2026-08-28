from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import math
import os
import re
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings

from .exceptions import SecurityConfigurationError


_DIGIT_TRANSLATION = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)
_REMOVABLE_SEPARATORS = re.compile(r"[\s\-‐‑‒–—―_/\\.,،٬()]+")
_NATIONAL_ID_PATTERN = re.compile(r"^[0-9]{10}$")
_SAUDI_MOBILE_PATTERN = re.compile(r"^05[0-9]{8}$")
_POTENTIAL_SENSITIVE_DIGIT_RUN = re.compile(
    r"(?<![0-9٠-٩۰-۹])[0-9٠-٩۰-۹]{10,}(?![0-9٠-٩۰-۹])"
)
_KEY_BYTES = 32
_NONCE_BYTES = 12
_AAD_PREFIX = b"attendance-portal"


@dataclass(frozen=True, slots=True)
class EncryptedValue:
    ciphertext: bytes
    key_version: str


def _cryptography_primitives():
    """Load the optional runtime dependency only when the feature is invoked."""

    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise SecurityConfigurationError(
            "مكوّن حماية البيانات غير متاح. تواصل مع مسؤول النظام.",
            code="security_component_unavailable",
        ) from exc
    return AESGCM, InvalidTag


def _setting_or_environment(name: str) -> str:
    value = getattr(settings, name, None) or os.environ.get(name)
    if not isinstance(value, str) or not value.strip():
        raise SecurityConfigurationError(code="security_configuration_missing")
    return value.strip()


def _decode_key(name: str) -> bytes:
    encoded = _setting_or_environment(name)
    try:
        padded = encoded + ("=" * (-len(encoded) % 4))
        key = base64.b64decode(padded, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SecurityConfigurationError(
            code="security_configuration_invalid"
        ) from exc
    if len(key) != _KEY_BYTES:
        raise SecurityConfigurationError(code="security_configuration_invalid")
    return key


def get_current_encryption_key_version() -> str:
    version = getattr(settings, "PII_ENCRYPTION_KEY_VERSION", None) or "v1"
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,30}", version):
        raise SecurityConfigurationError(code="security_configuration_invalid")
    return version


def ensure_crypto_configured() -> None:
    """Validate both independent keys only when the protected feature is used."""

    _cryptography_primitives()
    encryption_key = _decode_key("PII_ENCRYPTION_KEY")
    hmac_key = _decode_key("NATIONAL_ID_HMAC_KEY")
    if hmac.compare_digest(encryption_key, hmac_key):
        raise SecurityConfigurationError(code="security_keys_must_be_independent")
    get_current_encryption_key_version()


def _coerce_identifier(value: object) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (float, Decimal)):
        numeric = float(value)
        if not math.isfinite(numeric) or not numeric.is_integer():
            return str(value)
        return str(int(numeric))
    return str(value).strip()


def normalize_national_id(value: object) -> str:
    """Return the canonical ten ASCII digits or raise a non-sensitive error."""

    candidate = _coerce_identifier(value).translate(_DIGIT_TRANSLATION)
    candidate = _REMOVABLE_SEPARATORS.sub("", candidate)
    if not _NATIONAL_ID_PATTERN.fullmatch(candidate):
        raise ValueError("يجب أن يتكون السجل المدني من 10 أرقام.")
    return candidate


def normalize_saudi_mobile(value: object) -> str | None:
    """Normalize an optional Saudi mobile number to the 05XXXXXXXX form."""

    candidate = _coerce_identifier(value).translate(_DIGIT_TRANSLATION)
    candidate = _REMOVABLE_SEPARATORS.sub("", candidate)
    if not candidate:
        return None
    if candidate.startswith("+966"):
        candidate = "0" + candidate[4:]
    elif candidate.startswith("00966"):
        candidate = "0" + candidate[5:]
    elif candidate.startswith("966"):
        candidate = "0" + candidate[3:]
    if not _SAUDI_MOBILE_PATTERN.fullmatch(candidate):
        raise ValueError("رقم الجوال السعودي غير صحيح.")
    return candidate


def mask_national_id(normalized_national_id: str) -> str:
    return f"******{normalized_national_id[-4:]}"


def mask_mobile(normalized_mobile: str | None) -> str:
    if not normalized_mobile:
        return ""
    return f"{normalized_mobile[:2]}****{normalized_mobile[-4:]}"


def mask_untrusted_identifier(value: object) -> str:
    """Mask invalid input without ever returning the complete supplied value."""

    candidate = _coerce_identifier(value).translate(_DIGIT_TRANSLATION)
    digits = "".join(character for character in candidate if character.isdigit())
    return f"******{digits[-4:]}" if digits else "قيمة غير صالحة"


def redact_potential_national_ids(value: object) -> str:
    """Mask long digit runs in display metadata such as filenames/usernames."""

    rendered = "" if value is None else str(value)

    def replacement(match: re.Match[str]) -> str:
        normalized = match.group(0).translate(_DIGIT_TRANSLATION)
        return f"******{normalized[-4:]}"

    return _POTENTIAL_SENSITIVE_DIGIT_RUN.sub(replacement, rendered)


def national_id_digest(normalized_national_id: str) -> str:
    if not _NATIONAL_ID_PATTERN.fullmatch(normalized_national_id):
        raise ValueError("يجب تطبيع السجل المدني قبل إنشاء بصمة المطابقة.")
    key = _decode_key("NATIONAL_ID_HMAC_KEY")
    return hmac.new(
        key,
        normalized_national_id.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def _associated_data(context: str, key_version: str) -> bytes:
    if not context or len(context) > 200:
        raise ValueError("سياق التشفير غير صالح.")
    return b"|".join(
        (_AAD_PREFIX, context.encode("utf-8"), key_version.encode("ascii"))
    )


def encrypt_sensitive_text(
    plaintext: str,
    *,
    context: str,
    key_version: str | None = None,
) -> EncryptedValue:
    return encrypt_sensitive_bytes(
        plaintext.encode("utf-8"),
        context=context,
        key_version=key_version,
    )


def encrypt_sensitive_bytes(
    plaintext: bytes,
    *,
    context: str,
    key_version: str | None = None,
) -> EncryptedValue:
    version = key_version or get_current_encryption_key_version()
    key = _decode_key("PII_ENCRYPTION_KEY")
    aesgcm_class, _ = _cryptography_primitives()
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = aesgcm_class(key).encrypt(
        nonce,
        plaintext,
        _associated_data(context, version),
    )
    return EncryptedValue(ciphertext=nonce + ciphertext, key_version=version)


def decrypt_sensitive_text(
    encrypted_value: bytes,
    *,
    context: str,
    key_version: str,
) -> str:
    try:
        return decrypt_sensitive_bytes(
            encrypted_value,
            context=context,
            key_version=key_version,
        ).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("تعذر التحقق من سلامة البيانات المشفرة.") from exc


def decrypt_sensitive_bytes(
    encrypted_value: bytes,
    *,
    context: str,
    key_version: str,
) -> bytes:
    if len(encrypted_value) <= _NONCE_BYTES:
        raise ValueError("البيانات المشفرة غير صالحة.")
    key = _decode_key("PII_ENCRYPTION_KEY")
    aesgcm_class, invalid_tag = _cryptography_primitives()
    nonce = encrypted_value[:_NONCE_BYTES]
    ciphertext = encrypted_value[_NONCE_BYTES:]
    try:
        return aesgcm_class(key).decrypt(
            nonce,
            ciphertext,
            _associated_data(context, key_version),
        )
    except invalid_tag as exc:
        raise ValueError("تعذر التحقق من سلامة البيانات المشفرة.") from exc
