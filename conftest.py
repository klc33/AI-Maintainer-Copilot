"""Root pytest configuration.

Two jobs:

1. Put the project root on ``sys.path`` so tests can ``import app``,
   ``import tools``, ``import evals``, ``import prompts`` no matter how
   pytest was invoked (`pytest`, `python -m pytest`, inside Docker…).

2. Provide values for the environment variables that app modules read
   *at import time* (`app.db.session` reads ``DATABASE_URL`` as soon as it's
   imported). These are the **local-dev defaults** — the docker-compose
   credentials with the host-mapped ports — so the integration tests in
   tests/integration/ actually connect to the running stack rather than
   skipping. Unit/smoke tests never open a connection (the SQLAlchemy engine
   is lazy), so the values only need to be syntactically valid for them.

   ``setdefault`` means a real value already in the environment — CI, an
   exported override — always wins.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Local-dev defaults: docker-compose credentials reached over the host-mapped
# ports (db→localhost:5432, etc.). Never overrides a real environment.
_ENV_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://copilot:changeme@localhost:5432/copilot",
    "REDIS_URL": "redis://localhost:6379/0",
    "MODEL_SERVER_URL": "http://localhost:8001",
    "VAULT_ADDR": "http://localhost:8200",
    "VAULT_TOKEN": "root",
    "MINIO_ENDPOINT": "localhost:9000",
    "MINIO_ACCESS_KEY": "minioadmin",
    "MINIO_SECRET_KEY": "minioadmin",
}
for _key, _val in _ENV_DEFAULTS.items():
    os.environ.setdefault(_key, _val)
