"""What role a part of a document plays, read from its own structure (never from a list of answers).

navigation: the "In This Chapter" / "In This Document" / "Contents" lists. They repeat the headings of the document in short lines, so a
            heading-like question matches them almost perfectly; they point to content, they are not content, and are never used as an answer.
"""
import re

NAV_TITLE = re.compile(r"^\s*(?:in this (?:chapter|document)|contents|table of contents)\b", re.I)


# True if a title looks like a "Contents"/"In This Chapter" navigation heading.
def is_nav_title(title):
    return bool(NAV_TITLE.search(title or ""))


# True when any part of a passage's heading trail is a navigation list.
def is_nav_heading(trail):
    """True when any part of a passage's heading trail ("Chapter 33 > In This Chapter") is a navigation list."""
    return any(is_nav_title(part) for part in (trail or "").split(" > "))
