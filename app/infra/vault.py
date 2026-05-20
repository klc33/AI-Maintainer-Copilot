# app/infra/vault.py
import os
import hvac
from app.domain.exceptions import VaultError

class VaultClient:
    def __init__(self):
        self.url = os.environ.get("VAULT_ADDR", "http://vault:8200")
        self.token = os.environ.get("VAULT_TOKEN", "root")
        self._client = hvac.Client(url=self.url, token=self.token)
        self._cache = {}

    def health(self) -> bool:
        try:
            return self._client.sys.is_initialized() and not self._client.sys.is_sealed()
        except Exception:
            return False

    def load(self, path: str) -> dict:
        if path in self._cache:
            return self._cache[path]
        try:
            # hvac expects path relative to mount point, not including 'secret/'
            relative_path = path.replace("secret/", "", 1) if path.startswith("secret/") else path
            response = self._client.secrets.kv.v2.read_secret_version(
                path=relative_path, mount_point="secret"
            )
            data = response["data"]["data"]
            self._cache[path] = data
            return data
        except Exception as e:
            raise VaultError(f"Failed to read secret at {path}: {e}")

    def cached(self, path: str) -> dict:
        if path not in self._cache:
            raise VaultError(f"Secret {path} not in cache. Load it first.")
        return self._cache[path]

vault = VaultClient()