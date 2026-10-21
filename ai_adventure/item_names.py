"""Conservative singular labels for countable inventory stacks."""
import re


def singularize_item_name(name: str) -> str:
    """Converts a plural stack label to a singular label conservatively."""

    text = str(name or "").strip()
    suffix = re.search(r"\s+\([^()]*\)$", text)
    if suffix:
        return singularize_item_name(text[:suffix.start()]) + suffix.group()
    qualifier = re.search(r"\s+(?:of|with|for)\s+", text, re.IGNORECASE)
    if qualifier:
        return singularize_item_name(text[:qualifier.start()]) + text[qualifier.start():]
    match = re.match(r"^(.*\s)?([^\s]+)([.,;:]*)$", str(name or "").strip())
    if not match:
        return str(name or "").strip()
    prefix, word, punctuation = match.groups()
    lower = word.casefold()
    invariant_plurals = {
        "binoculars", "glasses", "jeans", "scissors", "series", "species",
        "tongs", "trousers", "pants", "shorts", "leggings", "tights", "clothes",
        "overalls", "pajamas", "pliers", "forceps", "shears", "pruners", "tweezers",
        "fish", "deer", "sheep", "salmon", "trout", "cod", "news", "lens", "chaos",
        "molasses", "oats", "asparagus", "couscous", "measles", "mumps",
        "canvas", "gas", "asbestos", "bellows", "gallows", "means", "headquarters",
    }
    irregular = {
        "calves": "calf", "children": "child", "elves": "elf", "feet": "foot",
        "geese": "goose", "halves": "half", "knives": "knife", "leaves": "leaf",
        "lives": "life", "loaves": "loaf", "men": "man", "mice": "mouse",
        "selves": "self", "shelves": "shelf", "teeth": "tooth", "wives": "wife",
        "wolves": "wolf", "women": "woman",
        "cookies": "cookie", "pies": "pie", "ties": "tie", "potatoes": "potato",
        "tomatoes": "tomato",
    }
    if lower in invariant_plurals:
        return str(name or "").strip()
    singular = irregular.get(lower)
    if singular is None:
        if lower.endswith("ies") and len(word) > 3:
            singular = word[:-3] + "y"
        elif lower.endswith(("ches", "shes", "sses", "xes", "zes")):
            singular = word[:-2]
        elif lower.endswith("s") and not lower.endswith(("ss", "us", "is", "ous")):
            singular = word[:-1]
        else:
            singular = word
    if word.isupper():
        singular = singular.upper()
    elif word.istitle():
        singular = singular.title()
    return f"{prefix or ''}{singular}{punctuation}"

