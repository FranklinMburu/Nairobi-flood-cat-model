"""Where the repository's shared folders (loss_engine/, data/, examples/) are.

Locally they sit beside backend/. On Vercel the Django function is built from
backend/ alone, so its build command copies them into backend/_shared/ (see
vercel.json). When that copy exists, it is used; otherwise the repository root.
"""

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
_SHARED = BACKEND_DIR / "_shared"

REPO_ROOT = _SHARED if (_SHARED / "loss_engine").is_dir() else BACKEND_DIR.parent
