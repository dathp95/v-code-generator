"""
Verify that an encrypted private key matches a public key PEM.
"""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey


def load_private_key(path: Path, passphrase: bytes) -> RSAPrivateKey:
    try:
        key = serialization.load_pem_private_key(path.expanduser().read_bytes(), password=passphrase)
    except Exception as error:
        raise ValueError("Unable to load encrypted private key.") from error
    if not isinstance(key, RSAPrivateKey):
        raise ValueError("Private key must be an RSA private key.")
    return key


def load_public_key(path: Path) -> RSAPublicKey:
    try:
        key = serialization.load_pem_public_key(path.expanduser().read_bytes())
    except Exception as error:
        raise ValueError("Unable to load public key.") from error
    if not isinstance(key, RSAPublicKey):
        raise ValueError("Public key must be an RSA public key.")
    return key


def public_key_bytes(public_key: RSAPublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def key_pair_matches(private_key_path: Path, public_key_path: Path, passphrase: bytes) -> bool:
    private_key = load_private_key(private_key_path, passphrase)
    public_key = load_public_key(public_key_path)
    return public_key_bytes(private_key.public_key()) == public_key_bytes(public_key)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify a V-CODE activation private/public key pair.")
    parser.add_argument("--private-key", required=True, type=Path, help="Encrypted private key PEM path.")
    parser.add_argument("--public-key", required=True, type=Path, help="Public key PEM path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    passphrase = getpass.getpass("Private key passphrase: ").encode("utf-8")
    try:
        matches = key_pair_matches(args.private_key, args.public_key, passphrase)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    print("MATCH" if matches else "MISMATCH")
    raise SystemExit(0 if matches else 1)


if __name__ == "__main__":
    main()
