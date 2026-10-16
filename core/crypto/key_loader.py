"""Load encrypted RSA keys without including sensitive details in errors."""

from pathlib import Path

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from core.exceptions import PrivateKeyLoadError


def load_private_key(path: Path, passphrase: bytes) -> RSAPrivateKey:
    if not isinstance(passphrase, bytes) or not passphrase:
        raise PrivateKeyLoadError("Unable to load encrypted private key.")
    try:
        key = serialization.load_pem_private_key(
            path.expanduser().read_bytes(), password=passphrase,
        )
    except (OSError, ValueError, TypeError, UnsupportedAlgorithm):
        raise PrivateKeyLoadError("Unable to load encrypted private key.") from None
    if not isinstance(key, RSAPrivateKey):
        raise PrivateKeyLoadError("Private key must be an RSA private key.")
    return key
