import re
import unicodedata


def normalize_entity(s: str) -> str:
    """Normalize an entity string for overlap comparison."""
    # Unicode NFKC normalization
    s = unicodedata.normalize("NFKC", s)
    # Casefold (aggressive lowercasing)
    s = s.casefold()
    # Collapse internal whitespace
    s = re.sub(r"\s+", " ", s)
    # Strip leading/trailing punctuation (Unicode category P*) and whitespace
    s = s.strip()
    while s and (unicodedata.category(s[0]).startswith("P") or s[0].isspace()):
        s = s[1:].strip()
    while s and (unicodedata.category(s[-1]).startswith("P") or s[-1].isspace()):
        s = s[:-1].strip()
    # Remove a leading 'the '
    if s.startswith("the "):
        s = s[4:]
    return s
