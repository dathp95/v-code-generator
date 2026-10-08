"""
Generate a fresh RSA key pair for V-CODE activation keys.

Private keys are encrypted and must be stored outside this repository.
"""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


KEY_SIZE = 3072
PUBLIC_EXPONENT = 65537
PRIVATE_KEY_NAME = "vcode-activation-private.pem"
PUBLIC_KEY_NAME = "vcode-activation-public.pem"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an encrypted RSA 3072-bit key pair for V-CODE activation.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Secure admin directory where the key pair will be written.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow overwriting existing key files after confirmation.",
    )
    return parser.parse_args()


def prompt_passphrase() -> bytes:
    passphrase = getpass.getpass("Private key passphrase: ")
    confirmation = getpass.getpass("Confirm private key passphrase: ")
    if passphrase != confirmation:
        raise SystemExit("Passphrases do not match.")
    if len(passphrase) < 12:
        raise SystemExit("Passphrase must be at least 12 characters.")
    return passphrase.encode("utf-8")


def confirm_overwrite(paths: list[Path], force: bool) -> None:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return

    existing_text = "\n".join(f"  - {path}" for path in existing)
    if not force:
        raise SystemExit(
            "Refusing to overwrite existing key file(s):\n"
            f"{existing_text}\n"
            "Re-run with --force if replacement is intentional."
        )

    answer = input("Overwrite existing key file(s)? Type YES to continue: ")
    if answer != "YES":
        raise SystemExit("Key generation cancelled.")


def generate_key_pair(output_dir: Path, passphrase: bytes, force: bool = False) -> tuple[Path, Path]:
    output_dir = output_dir.expanduser().resolve()
    private_key_path = output_dir / PRIVATE_KEY_NAME
    public_key_path = output_dir / PUBLIC_KEY_NAME

    confirm_overwrite([private_key_path, public_key_path], force)
    output_dir.mkdir(parents=True, exist_ok=True)

    private_key = rsa.generate_private_key(
        public_exponent=PUBLIC_EXPONENT,
        key_size=KEY_SIZE,
    )
    public_key = private_key.public_key()

    private_key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(passphrase),
        )
    )
    public_key_path.write_bytes(
        public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    return private_key_path, public_key_path


def main() -> None:
    args = parse_args()
    private_key_path, public_key_path = generate_key_pair(
        args.output_dir,
        prompt_passphrase(),
        force=args.force,
    )

    print("Activation key pair generated.")
    print(f"Private key: {private_key_path}")
    print(f"Public key : {public_key_path}")
    print()
    print("Keep the private key offline and outside Git.")
    print("Embed the public key PEM bytes into UDS-Analysis/license/trusted_public_key.py.")


if __name__ == "__main__":
    main()
