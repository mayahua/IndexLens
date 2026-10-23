"""Record source code provenance for research calculations."""

import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Any


def code_state(root: Path) -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL
        ).strip()

    package = Path(__file__).resolve().parents[1]
    hashes = {
        "src/indexlens/" + str(p.relative_to(package)): sha256(
            p.read_bytes()
        ).hexdigest()
        for p in sorted(package.rglob("*.py"))
    }
    if (root / "pyproject.toml").is_file():
        hashes["pyproject.toml"] = sha256(
            (root / "pyproject.toml").read_bytes()
        ).hexdigest()
    result: dict[str, Any] = dict(file_sha256=hashes)
    try:
        status = git("status", "--porcelain=v1", "--untracked-files=all")
        result.update(
            revision=git("rev-parse", "HEAD"),
            working_tree_status=status,
            dirty=bool(status),
        )
    except (OSError, subprocess.CalledProcessError):
        result.update(revision=None, working_tree_status=None, dirty=None)
    return result
