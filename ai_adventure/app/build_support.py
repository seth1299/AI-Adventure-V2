"""Small build-time helpers that keep release artifacts usable."""

from __future__ import annotations

import shutil
from pathlib import Path


def copy_env_to_dist(project_root: Path) -> Path:
    """Copies the project's required .env file beside the built executable."""

    source = project_root / ".env"

    if not source.is_file():
        raise FileNotFoundError(f"Required build configuration file is missing: {source}")

    destination = project_root / "dist" / ".env"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def main() -> int:
    """Copies .env to dist for the batch build scripts."""

    project_root = Path(__file__).resolve().parents[2]

    try:
        copy_env_to_dist(project_root)
    except OSError as error:
        print(f"ERROR: Could not copy .env to dist: {error}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
