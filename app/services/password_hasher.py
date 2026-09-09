from pwdlib import PasswordHash


class PasswordHasher:
    """Own the password hashing algorithm and its configuration."""

    def __init__(self):
        self._hasher = PasswordHash.recommended()

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)
