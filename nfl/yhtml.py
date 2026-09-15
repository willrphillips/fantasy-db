"""Minimal HTML table extraction on the stdlib parser. Yahoo nests whole tables (weather
forecasts) inside player cells, so a regex over <tr> falls apart; this tracks depth.

    for t in tables(html): t["attrs"], t["rows"] -> [[Cell, ...], ...]
    Cell.text  visible text with nested tables removed
    Cell.html  raw inner HTML (nested tables included)
"""
from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser

_tag = re.compile(r"<[^>]+>")
_ws = re.compile(r"\s+")
_nested = re.compile(r"<table\b.*?</table>", re.S)


def text_of(fragment: str) -> str:
    fragment = _nested.sub(" ", fragment)
    return _ws.sub(" ", _html.unescape(_tag.sub(" ", fragment))).strip()


class Cell:
    __slots__ = ("tag", "attrs", "html", "_text")

    def __init__(self, tag, attrs, html):
        self.tag, self.attrs, self.html, self._text = tag, dict(attrs), html, None

    @property
    def text(self):
        if self._text is None:
            self._text = text_of(self.html)
        return self._text

    def __repr__(self):
        return f"Cell({self.text[:30]!r})"


class _Parser(HTMLParser):
    def __init__(self, src):
        super().__init__(convert_charrefs=False)
        self.src = src
        self.line_off = [0]
        for m in re.finditer("\n", src):
            self.line_off.append(m.end())
        self.tables = []
        self.stack = []

    def _off(self):
        line, col = self.getpos()
        return self.line_off[line - 1] + col

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.stack.append({"attrs": dict(attrs), "rows": [], "row": None, "cell": None})
            return
        if not self.stack:
            return
        t = self.stack[-1]
        if tag == "tr":
            if t["cell"] is not None:
                self._close_cell(t, self._off())
            if t["row"] is not None:
                t["rows"].append(t["row"])
            t["row"] = []
        elif tag in ("td", "th"):
            if t["cell"] is not None:
                self._close_cell(t, self._off())
            if t["row"] is None:
                t["row"] = []
            start = self.src.find(">", self._off()) + 1
            t["cell"] = (tag, attrs, start)

    def _close_cell(self, t, end):
        tag, attrs, start = t["cell"]
        t["row"].append(Cell(tag, attrs, self.src[start:end]))
        t["cell"] = None

    def handle_endtag(self, tag):
        if not self.stack:
            return
        t = self.stack[-1]
        if tag in ("td", "th") and t["cell"] is not None:
            self._close_cell(t, self._off())
        elif tag == "tr":
            if t["cell"] is not None:
                self._close_cell(t, self._off())
            if t["row"] is not None:
                t["rows"].append(t["row"])
            t["row"] = None
        elif tag == "table":
            if t["cell"] is not None:
                self._close_cell(t, self._off())
            if t["row"] is not None:
                t["rows"].append(t["row"])
            self.stack.pop()
            self.tables.append({"attrs": t["attrs"], "rows": t["rows"]})


def tables(src: str) -> list:
    p = _Parser(src)
    p.feed(src)
    p.close()
    return p.tables


def find_table(tbls: list, id: str = None, cls: str = None):
    for t in tbls:
        a = t["attrs"]
        if id and a.get("id") == id:
            return t
        if cls and cls in (a.get("class") or "").split():
            return t
    return None


def header_index(rows: list, *labels: str) -> dict:
    """Scan header rows for cells whose text starts with a label; -> {label: column index}."""
    out = {}
    for row in rows:
        if not row or row[0].tag != "th":
            continue
        col = 0  # data-column position; a colspan header (Yahoo's "Action") covers 2 cells
        for c in row:
            for lab in labels:
                if lab not in out and c.text.lower().startswith(lab.lower()):
                    out[lab] = col
            try:
                col += max(1, int(c.attrs.get("colspan") or 1))
            except ValueError:
                col += 1
    return out
