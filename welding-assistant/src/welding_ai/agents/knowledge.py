"""The documents as the agents see them: tables by document and section, and passages as cited sources.

An agent never holds a fact of its own. It asks for a table row or a passage and quotes it, with the chunk the reader can open.
"""
import re


def _squash(text):
    return re.sub(r"\s+", " ", text).strip().lower()


class Knowledge:
    def __init__(self, tables, index):
        """tables: the list of table dicts (tables.json); index: the search index (its chunks and its search())."""
        self.tables, self.index = tables, index

    def table(self, source_prefix, heading_part):
        """The first table of a document (by file-name prefix, "20_") whose section heading contains the words; None when it is not there."""
        want = _squash(heading_part)
        return next((t for t in self.tables if t["source"].startswith(source_prefix) and want in _squash(t["heading"])), None)

    def row(self, table, first_cell):
        """The row whose first cell starts with the words (case-insensitive), as a {column: cell} dict; None when absent."""
        if table is None:
            return None
        want = _squash(first_cell)
        for r in table["rows"]:
            if _squash(r[0]).startswith(want):
                return dict(zip(table["header"], r))
        return None

    def hit(self, chunk_id, focus_on=""):
        """A source entry for the sources panel: the passage the answer rests on, trimmed around `focus_on`."""
        c = self.index.chunks[chunk_id]
        text = self.index.focus(c["text"], focus_on, 900) if focus_on else c["text"][:900]
        return {"chunk_id": chunk_id, "source": c["source"], "heading": c["heading"], "text": text, "score": 1.0, "coverage": 1.0, "entity_match": False, "units": []}

    def cite(self, hit):
        return {"source": hit["source"], "heading": hit["heading"], "chunk": hit["chunk_id"]}
