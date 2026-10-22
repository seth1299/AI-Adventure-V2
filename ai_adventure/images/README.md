# Bundled default images

Add generic item art under `items/` and skill art under `skills/`. Subdirectories
are supported. Use descriptive filenames such as `items/Backpack.png` or
`skills/Alchemy_Icon.png`; no code changes or manifest entries are required.
PNG (including transparency), JPEG, WebP, and BMP are discovered automatically.
Prefer clear ASCII filenames because new-game text contracts use ASCII.

Both builds include this directory. Rebuild after adding artwork for a release.
Source runs discover files directly. Gemini receives the relative filenames and
may select `default_image` on a newly created item or skill. Selection is optional:
no suitable art means no selection. The application validates the filename and
category, saves the selection, and uses it as a fallback when there is no usable
uploaded/generated image. Generation settings do not disable bundled art.

Characters and locations do not use this catalog. Artwork is read from the
bundle without modifying the supplied originals. Display sizing preserves its
proportions and transparency; you do not need to prepare 32-pixel source files.
