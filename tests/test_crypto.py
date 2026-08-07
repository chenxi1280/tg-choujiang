import base64
import os

import pytest
from cryptography.exceptions import InvalidTag

from tg_lottery.telegram.crypto import SecretCipher


def test_cipher_round_trip_and_context_binding() -> None:
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    cipher = SecretCipher(key)
    encrypted = cipher.encrypt("https://t.me/+secret", context="invite-link:1")
    assert cipher.decrypt(encrypted, context="invite-link:1") == "https://t.me/+secret"
    with pytest.raises(InvalidTag):
        cipher.decrypt(encrypted, context="invite-link:2")
