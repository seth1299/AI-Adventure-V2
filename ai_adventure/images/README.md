# Bundled default images

Use descriptive filenames and these directories to classify artwork:

- `locations/game_genres/fantasy/art_styles/digital_art/cottage.png`
- `items/game_genres/fantasy/art_styles/digital_art/leather_backpack.png`
- `skills/game_genres/cyberpunk/art_styles/watercolor/lockpicking.png`

The app matches the adventure's genre and art style to the folder names and the
subject to the filename. Genre labels such as `Science fiction` use folder names
such as `science_fiction`. Use an image style identifier such as `digital_art`,
`watercolor`, or `film_noir`. Locations require both descriptors. Item art and
skill icons outside those folders are available as generic fallbacks. PNG, JPEG,
WebP, and BMP files are discovered automatically.

Both builds include this directory. Rebuild after adding artwork for a release.
Source runs discover files directly. Gemini receives eligible relative paths and
may select `default_image` for a new item or skill. The app can match themed art
locally, including by the type named in a location's description. Existing uploads
and generated art take precedence in their save. New games preview eligible
bundled and cross-save images before offering generation. Bundled art also works
when generation is disabled.

Cross-save reuse checks the saved genre, art style, subject, and resolution of
the cached image. Cached artwork that predates genre tracking is still usable in
its original save. Bundled files are read in place, preserving the originals.
