import os
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Unit tests must never call the shared NSTL model service. Integration tests
# opt in explicitly and use a real or recording client as appropriate.
os.environ.setdefault("CONTENT_ASSESSMENT_LLM_ENABLED", "false")
os.environ.setdefault("LITERATURE_FULLTEXT_API_URL", "")
# Set before application imports: TestClient startup and every unpatched
# repository access must stay away from the shared deployment database.
os.environ["USE_MEMORY_STORE"] = "true"
