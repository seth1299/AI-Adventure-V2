"""Canonical infrastructure boundary for generated visual assets."""

from ai_adventure.visual_assets import (
    DEFAULT_IMAGE_LIMIT,
    GeminiVisualAssetService,
    VisualAssetRequest,
    build_visual_asset_requests,
    find_reusable_inventory_asset,
    save_relative_image_filename,
    save_scaled_jpeg,
)

__all__ = [
    "DEFAULT_IMAGE_LIMIT",
    "GeminiVisualAssetService",
    "VisualAssetRequest",
    "build_visual_asset_requests",
    "find_reusable_inventory_asset",
    "save_relative_image_filename",
    "save_scaled_jpeg",
]
