#!/usr/bin/env python3
"""Generate RSA key pair for CollabTrack LTI 1.3 tool registration."""

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def main() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )

    print("Add these to your backend .env (use \\n for newlines in single-line values):\n")
    print("LTI_TOOL_PRIVATE_KEY=" + private_pem.replace("\n", "\\n"))
    print()
    print("LTI_TOOL_PUBLIC_KEY=" + public_pem.replace("\n", "\\n"))


if __name__ == "__main__":
    main()
