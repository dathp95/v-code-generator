"""
generate_keys.py

Generate RSA key pair for Python UDS Analyzer License System.

Run this script ONLY ONCE.

Output:
    - private.pem
    - public.pem
"""

from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


# =============================================================================
# Configuration
# =============================================================================

KEY_SIZE = 2048
PUBLIC_EXPONENT = 65537

PRIVATE_KEY_FILE = Path("private.pem")
PUBLIC_KEY_FILE = Path("public.pem")


# =============================================================================
# Main
# =============================================================================

def generate_key_pair() -> None:
    """
    Generate RSA key pair.
    """

    print("Generating RSA key pair...")

    private_key = rsa.generate_private_key(
        public_exponent=PUBLIC_EXPONENT,
        key_size=KEY_SIZE,
    )

    public_key = private_key.public_key()

    PRIVATE_KEY_FILE.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    PUBLIC_KEY_FILE.write_bytes(
        public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    print("Done.")
    print(f"Private Key : {PRIVATE_KEY_FILE.resolve()}")
    print(f"Public Key  : {PUBLIC_KEY_FILE.resolve()}")


if __name__ == "__main__":
    generate_key_pair()