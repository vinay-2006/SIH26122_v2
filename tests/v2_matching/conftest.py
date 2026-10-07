import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "schedule_import"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v2_domain"))
from v2api import api, clean_db, client, client_500, world  # noqa: E402,F401
from domainkit import kit  # noqa: E402,F401
