import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12


class SecretCipher:
    def __init__(self, encoded_key: str) -> None:
        key = base64.urlsafe_b64decode(encoded_key.encode())
        if len(key) != 32:
            raise ValueError("APP_ENCRYPTION_KEY must encode exactly 32 bytes")
        self._cipher = AESGCM(key)

    def encrypt(self, plaintext: str, *, context: str) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        encrypted = self._cipher.encrypt(nonce, plaintext.encode(), context.encode())
        return nonce + encrypted

    def decrypt(self, encrypted: bytes, *, context: str) -> str:
        nonce = encrypted[:_NONCE_BYTES]
        ciphertext = encrypted[_NONCE_BYTES:]
        plaintext = self._cipher.decrypt(nonce, ciphertext, context.encode())
        return plaintext.decode()
