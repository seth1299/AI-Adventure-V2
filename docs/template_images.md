# Images in New Game Templates

When a New Game has an associated saved template, choosing an upload in the
final image-source dialog also saves a portable PNG copy inside that template's
JSON. This applies to items, skills, characters, NPCs, locations, and creatures.
Generated images and bundled fallback art are not captured as uploads.

Templates retain their form settings; saving an image only updates the template's
`template_images` attachments. The saved copy comes from the normalized image
cache, preserving transparency and small source dimensions. It contains no
reference to the user's original file, a save folder, or a generated runtime ID.
Deleting the source image or previous save/cache therefore does not break it.

On reuse, attachments survive the template editor, Save As, setup normalization,
and save creation. The final source-selection step restores uploads with the same
subject type and display name, even though new runtime IDs differ. Subjects that
are renamed, newly generated, or have invalid attachment data retain the normal
upload/generate/skip choices. Local image bytes are excluded from Gemini prompts.

Unchanged loaded templates remain associated for image updates without replacing
their form data. Explicit overwrite and Save As associate the selected target.
Choosing to use modified settings without saving does not update the source
template. If image storage succeeds for the game but template storage fails, the
app reports that the template image was not saved.
