"""
Field-level encryption for sensitive data stored in PostgreSQL.
Uses Fernet symmetric encryption derived from the app SECRET_KEY.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from config import settings
from core.logger import get_logger

logger = get_logger("encryption")

# Derive a stable 32-byte Fernet key from the app SECRET_KEY
_raw = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
_FERNET_KEY = base64.urlsafe_b64encode(_raw)
_fernet = Fernet(_FERNET_KEY)


def encrypt_value(plaintext: str) -> str:
    """Encrypt a string and return a base64-encoded ciphertext."""
    if not plaintext:
        return plaintext
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext back to plaintext."""
    if not ciphertext:
        return ciphertext
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        # Value was stored before encryption was enabled — return as-is
        logger.warning("Failed to decrypt value (may be legacy plaintext)")
        return ciphertext
