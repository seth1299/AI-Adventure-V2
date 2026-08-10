"""Small build-time helpers that keep release artifacts usable."""

from __future__ import annotations

from pathlib import Path


def prepare_dist_directory(project_root: Path) -> Path:
    """Ensures the build output directory exists without copying secrets."""

    destination = project_root / "dist"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def main() -> int:
    """Prepares the build output directory for the batch build scripts."""

    project_root = Path(__file__).resolve().parents[2]

    try:
        prepare_dist_directory(project_root)
    except OSError as error:
        print(f"ERROR: Could not prepare the dist directory: {error}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
