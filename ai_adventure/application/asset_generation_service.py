"""Application orchestration for generated visual assets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_adventure.infrastructure.sqlite import SaveRepository
from ai_adventure.infrastructure.images import (
    VisualAssetRequest,
    build_visual_asset_requests,
    find_reusable_visual_asset,
    save_relative_image_filename,
)


class AssetGenerationService:
    """Provides UI-neutral visual-asset discovery and local reuse operations."""

    @staticmethod
    def requests_for(
        repository: SaveRepository, *, include_player_without_appearance: bool = False,
    ) -> list[VisualAssetRequest]:
        return build_visual_asset_requests(repository, include_player_without_appearance=include_player_without_appearance)

    @staticmethod
    def target_path(
        repository: SaveRepository,
        request: VisualAssetRequest,
        images_dir: Path,
    ) -> Path:
        return images_dir / save_relative_image_filename(repository, request)

    @staticmethod
    def find_local_reuse(
        *,
        images_dir: Path,
        saves_dir: Path,
        repository: SaveRepository,
        request: VisualAssetRequest,
    ) -> dict[str, Any] | None:
        return find_reusable_visual_asset(
            images_dir=images_dir,
            saves_dir=saves_dir,
            repository=repository,
            request=request,
        )
