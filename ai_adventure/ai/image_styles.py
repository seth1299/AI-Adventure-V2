from __future__ import annotations

from collections.abc import Mapping
from typing import Any


DEFAULT_IMAGE_STYLE = "digital_art"

IMAGE_STYLE_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "value": "digital_art",
        "label": "Digital Art",
        "description": (
            "Polished semi-realistic digital game art with painterly texture and "
            "clear, readable forms."
        ),
        "prompt": (
            "Use polished semi-realistic digital painting, painterly texture, "
            "believable lighting, and clearly readable forms."
        ),
    },
    {
        "value": "photorealistic",
        "label": "Photorealistic",
        "description": (
            "Natural photographic realism with believable materials, lighting, and "
            "period-appropriate details."
        ),
        "prompt": (
            "Use natural photographic realism with believable materials, optics, "
            "lighting, surface wear, and period-appropriate details."
        ),
    },
    {
        "value": "film_noir",
        "label": "Film Noir",
        "description": (
            "Moody black-and-white noir imagery with expressive shadows and dramatic "
            "light."
        ),
        "prompt": (
            "Use classic black-and-white film noir aesthetics, expressive shadows, "
            "dramatic directional light, restrained grain, and a moody atmosphere."
        ),
    },
    {
        "value": "oil_painting",
        "label": "Oil Painting",
        "description": (
            "Traditional oil-painted imagery with visible brushwork, layered color, "
            "and tactile surfaces."
        ),
        "prompt": (
            "Use traditional oil-painting techniques with visible brushwork, layered "
            "color, tactile surfaces, and a hand-painted finish."
        ),
    },
    {
        "value": "watercolor",
        "label": "Watercolor",
        "description": (
            "Expressive watercolor washes with translucent color, paper texture, and "
            "soft edges."
        ),
        "prompt": (
            "Use expressive watercolor washes, translucent pigments, subtle paper "
            "texture, organic color variation, and selectively softened edges."
        ),
    },
    {
        "value": "comic_book",
        "label": "Comic Book",
        "description": (
            "Bold graphic illustration with confident inks, purposeful shading, and "
            "dynamic shapes."
        ),
        "prompt": (
            "Use bold comic-book illustration with confident ink contours, purposeful "
            "shading, dynamic shapes, and controlled graphic color."
        ),
    },
    {
        "value": "anime",
        "label": "Anime",
        "description": (
            "Clean anime-inspired illustration with expressive design and coherent "
            "cel-style rendering."
        ),
        "prompt": (
            "Use clean anime-inspired illustration, expressive but grounded design, "
            "coherent cel-style rendering, and carefully controlled detail."
        ),
    },
    {
        "value": "ink_illustration",
        "label": "Ink Illustration",
        "description": (
            "Hand-drawn ink work using varied line weight, hatching, and restrained "
            "color."
        ),
        "prompt": (
            "Use hand-drawn ink illustration with varied line weight, cross-hatching, "
            "organic marks, and restrained or monochrome color."
        ),
    },
    {
        "value": "pencil_sketch",
        "label": "Pencil Sketch",
        "description": (
            "Detailed graphite drawing with visible construction, shading, and paper "
            "grain."
        ),
        "prompt": (
            "Use a detailed graphite-pencil drawing with visible line variation, "
            "layered shading, subtle construction marks, and paper grain."
        ),
    },
    {
        "value": "charcoal",
        "label": "Charcoal",
        "description": (
            "Textural charcoal art with loose marks, deep values, and expressive "
            "smudging."
        ),
        "prompt": (
            "Use textural charcoal drawing with loose hand-made marks, deep tonal "
            "values, expressive smudging, and visible paper tooth."
        ),
    },
    {
        "value": "crayon",
        "label": "Crayon",
        "description": (
            "Playful hand-drawn crayon art with waxy texture, uneven strokes, and vivid "
            "color."
        ),
        "prompt": (
            "Use hand-drawn wax-crayon art with uneven strokes, visible paper texture, "
            "simple shapes, and playful but intentional color."
        ),
    },
    {
        "value": "pixel_art",
        "label": "Pixel Art",
        "description": (
            "Deliberate game-ready pixel art with a limited palette and crisp, "
            "readable silhouettes."
        ),
        "prompt": (
            "Use deliberate game-ready pixel art with a limited palette, crisp hard "
            "pixel edges, readable silhouettes, and no smoothing or photorealistic texture."
        ),
    },
)

KNOWN_IMAGE_STYLES = frozenset(option["value"] for option in IMAGE_STYLE_OPTIONS)
_IMAGE_STYLE_BY_VALUE = {option["value"]: option for option in IMAGE_STYLE_OPTIONS}


def normalize_image_style(value: Any) -> str:
    """Returns a supported visual-style identifier."""

    style = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "noir": "film_noir",
        "noire": "film_noir",
        "photo_realistic": "photorealistic",
    }
    style = aliases.get(style, style)
    return style if style in KNOWN_IMAGE_STYLES else DEFAULT_IMAGE_STYLE


def image_style_metadata(value: Any) -> Mapping[str, str]:
    """Returns label, description, and prompt direction for a visual style."""

    return _IMAGE_STYLE_BY_VALUE[normalize_image_style(value)]
