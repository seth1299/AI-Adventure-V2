# Skills

Skills persist `attribute` alongside name, scope description, training level, XP,
and bonus. The value is one of the six player attributes. New Game's Related Stat
dropdown fills from the selected catalog skill and remains editable. Templates,
normalization, finalized setup, runtime skill events, state, and both story and
check-planning context carry the field. Player selections take precedence over
Gemini's finalized stat. Omitted attributes on ordinary repository updates retain
the existing association.

The related stat is the default for checks; an action may justify an explicitly
different attribute. Resolved checks retain their actual attribute. The skill
dialog's typical bonus combines the default stat modifier with training level.

Skill icons use the existing visual-asset pipeline under subject type `skill`,
keyed by the saved skill ID. They share image settings, generation limits,
source selection, upload persistence, and generation controls with other assets.
Generation requests a simple square symbol, readable at 32 pixels. The Stats
table scales the PNG for display; no ICO conversion is needed. Until an image is
available, the row displays a placeholder icon. Upload stays available when
Gemini image generation is disabled.
