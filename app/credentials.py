"""Opt-in Windows credential storage. No plaintext/keyring fallback is accepted."""
import sys


class TokenStore:
    service = "V-CODE License Manager Telegram"
    account = "desktop-bot"

    def __init__(self):
        self.backend = None
        try:
            if sys.platform == "win32":
                from keyring.backends.Windows import WinVaultKeyring
                backend = WinVaultKeyring()
                if backend.priority > 0:
                    self.backend = backend
        except Exception:
            pass

    @property
    def available(self):
        return self.backend is not None

    def read(self):
        try:
            return self.backend.get_password(self.service, self.account) or "" if self.available else ""
        except Exception:
            self.backend = None
            return ""

    def save(self, token, remember):
        if not self.available:
            return False
        try:
            if remember:
                self.backend.set_password(self.service, self.account, token)
            elif self.backend.get_password(self.service, self.account):
                self.backend.delete_password(self.service, self.account)
            return True
        except Exception:
            self.backend = None
            return False
