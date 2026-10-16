"""Application-independent activation issuance."""

from datetime import datetime

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from core.crypto.activation import (
    create_activation_payload,
    create_activation_token,
    validate_days,
)
from core.exceptions import ActivationValidationError
from core.models.issued_activation import IssuedActivation


class LicenseService:
    def __init__(self, private_key: RSAPrivateKey):
        self._private_key = private_key

    def issue_activation(
        self,
        device_id: str,
        customer: str,
        edition: str,
        days: int,
        issued_at: datetime | None = None,
    ) -> IssuedActivation:
        if validate_days(days) > 3650:
            raise ActivationValidationError("days must be between 1 and 3650.")
        payload = create_activation_payload(
            device_id=device_id, customer=customer, edition=edition,
            days=days, issued_at=issued_at,
        )
        return IssuedActivation(create_activation_token(payload, self._private_key), payload)
