"""The shared README standard, as code: parse a README's structure and check it against the standard.

This module is byte-identical in ml-service-template and ml-platform, like
`docs/governance/readme-standard.md` itself; each repository's
`scripts/check_readme.py` imports it and adds only what is that repository's
own — the status block's sources, any badge a local gate holds, and the facts
the quick-start rules need about it (`Repository`). It is standard-library
only, because ml-service-template runs its gates on a bare `python3`.

**Why a parser, and why this one.** The first checker counted `## ` lines;
QA-4 round seventeen passed it with a setext heading, an HTML `<h2>`, an
unlinked claim badge and commands chained on one line. Its replacement read
lines with CommonMark's rules in mind, and round eighteen passed THAT with an
image in the title line, a reference definition inside a blockquote or with
its URL on the next line, and commands in `<pre>` or in a fence indented under
a list item. Each was a place where a line reader and a Markdown reader
disagree. So this is CommonMark's own block algorithm — containers matched
line by line, lazy continuation, the seven HTML block kinds, multi-line
reference definitions — followed by an inline pass that reads brackets, code
spans and escapes in the order CommonMark does, plus GitHub's tables.
ml-platform's tests hold it to markdown-it-py on the README, on an adversarial
corpus and on generated documents.

**Why the quick-start rules deny by default.** Round eighteen's `unpinned()`
recognised a list of installer spellings and passed everything else:
`python3.12 -m pip`, `uv tool install`, `@latest`, `>=0`, `sudo bash`, every
other package manager. Now a command passes only when a rule here reads it
and finds what it runs pinned; anything else fails and names itself, so a new
spelling is a change to this file (in both repositories), not a gap.

**Why the limits are read from the standard.** They are parsed from its text,
and the standard itself is pinned by its SHA-256 (`STANDARD_SHA256`),
identical in both repositories: changing it is changing this constant in
both, in the same session, which is what "shared byte for byte" means.
"""

from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

#: The SHA-256 of `docs/governance/readme-standard.md`, the same in both
#: repositories. A change to the standard is a change to this line in both.
STANDARD_SHA256 = "64215ca2e1929409ec04c2b3fa703cc8d76c1dc0ac2286aae0a211d3563e1a74"

STATUS_BEGIN = "<!-- BEGIN README STATUS -->"
STATUS_END = "<!-- END README STATUS -->"
RELATED_BEGIN = "<!-- BEGIN RELATED REPOSITORIES -->"
RELATED_END = "<!-- END RELATED REPOSITORIES -->"

_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

#: Badges that report a check rather than state a claim, matched on the WHOLE
#: image URL. Anchored at both ends: a prefix match let `python-100%25_tested`
#: through as a "Python" badge (round seventeen).
ALLOWED_BADGES = (
    # A workflow's status.
    re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/actions/workflows/[\w.-]+\.ya?ml/badge\.svg(?:\?[\w=&.%-]*)?"),
    # The latest release, the licence, read from GitHub by shields.
    re.compile(r"https://img\.shields\.io/github/(?:v/release|license)/[\w.-]+/[\w.-]+(?:\.svg)?(?:\?[\w=&.%-]*)?"),
    # A static licence badge: an SPDX-like identifier and a colour, nothing else.
    re.compile(
        r"https://img\.shields\.io/badge/[Ll]icen[cs]e(?::|-|%3A)(?:%20)?"
        r"(?:Apache|MIT|BSD|GPL|LGPL|AGPL|MPL|ISC|Unlicense)[\w.%-]*?-[a-z]+(?:\.svg)?"
    ),
    # A static Python badge: version numbers and separators, then a colour.
    re.compile(
        r"https://img\.shields\.io/badge/[Pp]ython-(?:\d+\.\d+(?:%2B|\+)?)"
        r"(?:(?:_%7C_|%20%7C%20|_\|_|%2C|,|_|%20)(?:\d+\.\d+(?:%2B|\+)?))*-[a-z]+(?:\.svg)?"
    ),
)

#: Where a badge comes from, so one placed outside the title area is found even
#: when it reports a check.
_BADGE_HOSTS = re.compile(r"img\.shields\.io|badgen\.net|/badge\.svg|codecov\.io/.+/badge|badge\.fury\.io", re.I)


@dataclass
class Heading:
    level: int
    text: str
    line: int
    spelling: str  # "atx", "setext", "html", or "nested" (inside a list item or a blockquote)


@dataclass
class Image:
    url: str
    line: int


@dataclass
class Structure:
    headings: list[Heading] = field(default_factory=list)
    images: list[Image] = field(default_factory=list)
    #: (line number, text) of every line a reader sees as code: fenced and
    #: indented code blocks, and the inside of `<pre>`.
    code: list[tuple[int, str]] = field(default_factory=list)


# --- block structure: CommonMark's container algorithm -----------------------
#
# A port of the algorithm cmark and commonmark.js implement (spec 0.31, "Appendix:
# A parsing strategy"), reduced to what the standard reads, with GitHub's table
# extension as markdown-it-py implements it. Where implementations disagree, it
# follows cmark-gfm, which renders the READMEs: each such point below was
# settled against GitHub's own renderer (`gh api /markdown`). Tabs are expanded
# to the next multiple of four in a line's leading run of whitespace and
# container markers, which is how the spec defines them for block structure.

_MATCHED, _FAILED, _CONSUMED = 0, 1, 2
_CONTAINER, _LEAF = 1, 2
_ACCEPTS_LINES = ("paragraph", "table", "fence", "indented", "html")

_MAYBE_SPECIAL = re.compile(r"[#`~*+_=<>0-9|:-]")
_ATX_OPEN = re.compile(r"#{1,6}(?:[ \t]+|$)")
_FENCE_OPEN = re.compile(r"`{3,}(?!.*`)|~{3,}")
_FENCE_CLOSE = re.compile(r"(`{3,}|~{3,})[ \t]*$")
_SETEXT = re.compile(r"(?:=+|-+)[ \t]*$")
_THEMATIC = re.compile(r"(?:(?:\*[ \t]*){3,}|(?:_[ \t]*){3,}|(?:-[ \t]*){3,})$")
_BULLET = re.compile(r"[*+-]")
_ORDERED = re.compile(r"(\d{1,9})[.)]")
_DELIMITER_CELL = re.compile(r":?-+:?")
_TAB_PREFIX = re.compile(r"[ \t>*+\-.)0-9]*")

#: The block-level tag names of HTML block kind 6 — GitHub's list (CommonMark
#: 0.29: `source`, not 0.31's `search`), checked against its renderer.
_BLOCK_TAGS = (
    "address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|details|dialog|dir|div|dl|dt"
    "|fieldset|figcaption|figure|footer|form|frame|frameset|h1|h2|h3|h4|h5|h6|head|header|hr|html|iframe|legend|li"
    "|link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|section|source|summary|table|tbody|td"
    "|tfoot|th|thead|title|tr|track|ul"
)
_ATTRIBUTE = r"""(?:\s+[a-zA-Z_:][a-zA-Z0-9:._-]*(?:\s*=\s*(?:[^"'=<>`\x00-\x20]+|'[^']*'|"[^"]*"))?)"""
_OPEN_TAG = r"<[A-Za-z][A-Za-z0-9-]*" + _ATTRIBUTE + r"*\s*/?>"
_CLOSE_TAG = r"</[A-Za-z][A-Za-z0-9-]*\s*>"
#: The seven HTML block kinds: how each starts, and how it ends (None: at a blank line).
_HTML_BLOCKS: tuple[tuple[re.Pattern[str], re.Pattern[str] | None], ...] = (
    (
        re.compile(r"<(?:script|pre|style|textarea)(?=\s|>|$)", re.I),
        re.compile(r"</(?:script|pre|style|textarea)>", re.I),
    ),
    (re.compile(r"<!--"), re.compile(r"-->")),
    (re.compile(r"<\?"), re.compile(r"\?>")),
    (re.compile(r"<![A-Za-z]"), re.compile(r">")),
    (re.compile(r"<!\[CDATA\["), re.compile(r"\]\]>")),
    (re.compile(rf"</?(?:{_BLOCK_TAGS})(?=\s|/?>|$)", re.I), None),
    (re.compile(rf"(?:{_OPEN_TAG}|{_CLOSE_TAG})\s*$"), None),
)


def _expand_tabs(line: str) -> str:
    prefix = _TAB_PREFIX.match(line)
    end = prefix.end() if prefix else 0
    if "\t" not in line[:end]:
        return line
    expanded = ""
    for char in line[:end]:
        expanded += " " * (4 - len(expanded) % 4) if char == "\t" else char
    return expanded + line[end:]


@dataclass(eq=False)
class _Block:
    kind: str
    parent: _Block | None
    line: int
    children: list[_Block] = field(default_factory=list)
    lines: list[tuple[int, str]] = field(default_factory=list)
    open: bool = True
    offset: int = 0  # item: the column its content starts at; fence: the opening fence's indentation
    fence: str = ""
    html: int = 0
    level: int = 0
    setext: bool = False
    info_seen: bool = False

    def nested(self) -> bool:
        ancestor = self.parent
        while ancestor is not None:
            if ancestor.kind in ("blockquote", "item"):
                return True
            ancestor = ancestor.parent
        return False


class _BlockParser:
    def __init__(self) -> None:
        self.document = _Block("document", None, 0)
        self.tip = self.document
        self.definitions: dict[str, str] = {}
        self.number = 0
        self.line = ""
        self.offset = 0
        self.next_nonspace = 0
        self.indent = 0
        self.blank = False
        self.all_closed = True
        self.last_matched = self.document

    # -- the line being read

    def _find_next_nonspace(self) -> None:
        index = self.offset
        while index < len(self.line) and self.line[index] == " ":
            index += 1
        self.next_nonspace, self.indent = index, index - self.offset
        self.blank = index >= len(self.line)

    def _to_nonspace(self) -> None:
        self.offset = self.next_nonspace

    def _at(self, index: int) -> str:
        return self.line[index : index + 1]

    # -- the tree

    def _close(self, block: _Block) -> None:
        block.open = False
        if block.kind == "paragraph":
            block.lines = self._take_definitions(block.lines)
        assert block.parent is not None
        self.tip = block.parent

    def _close_unmatched(self) -> None:
        if not self.all_closed:
            while self.tip is not self.last_matched:
                self._close(self.tip)
            self.all_closed = True

    def _add_child(self, kind: str) -> _Block:
        while self.tip.kind not in ("document", "blockquote", "item"):
            self._close(self.tip)
        block = _Block(kind, self.tip, self.number)
        self.tip.children.append(block)
        self.tip = block
        return block

    def _add_line(self, block: _Block) -> None:
        text = self.line[self.offset :]
        if block.kind == "fence" and not block.info_seen:
            block.info_seen = True
            return
        block.lines.append((self.number, text))
        if block.kind == "html" and block.html <= 5:
            closing = _HTML_BLOCKS[block.html - 1][1]
            if closing is not None and closing.search(text):
                self._close(block)

    # -- one line

    def feed(self, number: int, raw: str) -> None:
        self.number, self.line, self.offset = number, _expand_tabs(raw), 0
        container = self.document
        while container.children and container.children[-1].open:
            child = container.children[-1]
            self._find_next_nonspace()
            outcome = self._continue(child)
            if outcome == _CONSUMED:
                return
            if outcome == _FAILED:
                break
            container = child
        self.all_closed = container is self.tip
        self.last_matched = container

        matched_leaf = container.kind in ("fence", "indented", "html")
        starts = (
            self._table,
            self._blockquote,
            self._atx,
            self._fence,
            self._html,
            self._setext,
            self._thematic,
            self._item,
            self._indented,
        )
        while not matched_leaf:
            self._find_next_nonspace()
            if self.indent < 4 and not _MAYBE_SPECIAL.match(self.line, self.next_nonspace):
                self._to_nonspace()
                break
            for start in starts:
                outcome = start(container)
                if outcome:
                    container, matched_leaf = self.tip, outcome == _LEAF
                    break
            else:
                self._to_nonspace()
                break

        if not self.all_closed and not self.blank and self.tip.kind == "paragraph":
            self._add_line(self.tip)  # a lazy continuation line
            return
        self._close_unmatched()
        if container.kind in _ACCEPTS_LINES:
            self._add_line(container)
        elif self.offset < len(self.line) and not self.blank:
            self._to_nonspace()
            self._add_line(self._add_child("paragraph"))

    def finish(self) -> _Block:
        while self.tip is not self.document:
            self._close(self.tip)
        return self.document

    def _continue(self, block: _Block) -> int:
        kind = block.kind
        if kind == "blockquote":
            if self.indent < 4 and self._at(self.next_nonspace) == ">":
                self.offset = self.next_nonspace + 1
                if self._at(self.offset) == " ":
                    self.offset += 1
                return _MATCHED
            return _FAILED
        if kind == "item":
            if self.blank:
                if not block.children:
                    return _FAILED  # an item can begin with at most one blank line
                self._to_nonspace()
                return _MATCHED
            if self.indent >= block.offset:
                self.offset += block.offset
                return _MATCHED
            return _FAILED
        if kind == "fence":
            closing = _FENCE_CLOSE.match(self.line, self.next_nonspace) if self.indent < 4 else None
            if closing and closing.group(1)[0] == block.fence[0] and len(closing.group(1)) >= len(block.fence):
                self._close(block)
                return _CONSUMED
            skip = block.offset
            while skip > 0 and self._at(self.offset) == " ":
                self.offset, skip = self.offset + 1, skip - 1
            return _MATCHED
        if kind == "indented":
            if self.indent >= 4:
                self.offset += 4
                return _MATCHED
            if self.blank:
                self._to_nonspace()
                return _MATCHED
            return _FAILED
        if kind == "html":
            return _FAILED if self.blank and block.html >= 6 else _MATCHED
        if kind in ("paragraph", "table"):
            return _FAILED if self.blank else _MATCHED
        return _FAILED  # a heading or a thematic break is one line

    # -- block starts, in CommonMark's order (a table first, as markdown-it tries it)

    def _table(self, container: _Block) -> int:
        """GitHub's table: a paragraph's last line is the header when this line is a matching delimiter row."""
        if container.kind != "paragraph" or self.indent >= 4:
            return 0
        row = self.line[self.next_nonspace :].strip()
        if len(row) < 2 or row[0] not in "|-:" or (row[1] not in "|-: \t") or (row[0] == "-" and row[1] in " \t"):
            return 0
        if any(char not in "|-: \t" for char in row):
            return 0
        cells = row.split("|")
        aligns = 0
        for index, cell in enumerate(cells):
            cell = cell.strip()
            if not cell:
                if index in (0, len(cells) - 1):
                    continue
                return 0
            if not _DELIMITER_CELL.fullmatch(cell):
                return 0
            aligns += 1
        header_number, header = container.lines[-1]
        header = header.strip()
        if "|" not in header or len(_table_cells(header)) != aligns:
            return 0
        self._close_unmatched()
        container.lines = container.lines[:-1]
        if container.lines:
            self._close(container)
        else:
            assert container.parent is not None
            container.parent.children.remove(container)
            self.tip = container.parent
        table = self._add_child("table")
        table.line = header_number
        table.lines = [(header_number, header)]
        self.offset = len(self.line)
        return _LEAF

    def _blockquote(self, container: _Block) -> int:
        if self.indent < 4 and self._at(self.next_nonspace) == ">":
            self.offset = self.next_nonspace + 1
            if self._at(self.offset) == " ":
                self.offset += 1
            self._close_unmatched()
            self._add_child("blockquote")
            return _CONTAINER
        return 0

    def _atx(self, container: _Block) -> int:
        match = _ATX_OPEN.match(self.line, self.next_nonspace) if self.indent < 4 else None
        if not match:
            return 0
        self._close_unmatched()
        heading = self._add_child("heading")
        heading.level = len(match.group(0).rstrip(" \t"))
        content = re.sub(r"^[ \t]*#+[ \t]*$", "", self.line[match.end() :])
        heading.lines = [(self.number, re.sub(r"[ \t]+#+[ \t]*$", "", content))]
        self.offset = len(self.line)
        return _LEAF

    def _fence(self, container: _Block) -> int:
        match = _FENCE_OPEN.match(self.line, self.next_nonspace) if self.indent < 4 else None
        if not match:
            return 0
        self._close_unmatched()
        fence = self._add_child("fence")
        fence.fence, fence.offset = match.group(0), self.indent
        self.offset = self.next_nonspace + len(match.group(0))
        return _LEAF

    def _html(self, container: _Block) -> int:
        if self.indent >= 4 or self._at(self.next_nonspace) != "<":
            return 0
        rest = self.line[self.next_nonspace :]
        for kind, (opening, _) in enumerate(_HTML_BLOCKS, start=1):
            if not opening.match(rest):
                continue
            # Kind 7 cannot interrupt a paragraph it continues. It CAN follow one
            # whose container ended (a lazy line): cmark-gfm, which renders
            # GitHub's READMEs, and markdown-it both start the block there.
            if kind == 7 and container.kind in ("paragraph", "table"):
                return 0
            self._close_unmatched()
            self._add_child("html").html = kind
            return _LEAF
        return 0

    def _setext(self, container: _Block) -> int:
        if self.indent >= 4 or container.kind != "paragraph":
            return 0
        match = _SETEXT.match(self.line, self.next_nonspace)
        if not match:
            return 0
        self._close_unmatched()
        container.lines = self._take_definitions(container.lines)
        if not container.lines:
            return 0
        container.kind, container.setext = "heading", True
        container.level = 1 if match.group(0)[0] == "=" else 2
        container.line = container.lines[0][0]
        self.offset = len(self.line)
        return _LEAF

    def _thematic(self, container: _Block) -> int:
        if self.indent < 4 and _THEMATIC.match(self.line, self.next_nonspace):
            self._close_unmatched()
            self._add_child("thematic")
            self.offset = len(self.line)
            return _LEAF
        return 0

    def _item(self, container: _Block) -> int:
        if self.indent >= 4:
            return 0
        marker = _BULLET.match(self.line, self.next_nonspace) or _ORDERED.match(self.line, self.next_nonspace)
        if not marker or self._at(marker.end()) not in ("", " "):
            return 0
        if container.kind == "paragraph":
            # Interrupting a paragraph: not with an empty item, and an ordered one only from 1.
            if not self.line[marker.end() :].strip():
                return 0
            if marker.re is _ORDERED and int(marker.group(1)) != 1:
                return 0
        width = marker.end() - self.next_nonspace
        spaces = 0
        while spaces < 5 and self._at(marker.end() + spaces) == " ":
            spaces += 1
        empty = marker.end() + spaces >= len(self.line)
        padding = width + 1 if (empty or spaces >= 5 or spaces < 1) else width + spaces
        content = self.indent + padding
        self.offset = self.next_nonspace + padding
        self._close_unmatched()
        self._add_child("item").offset = content
        return _CONTAINER

    def _indented(self, container: _Block) -> int:
        if self.indent >= 4 and self.tip.kind != "paragraph" and not self.blank:
            self.offset += 4
            self._close_unmatched()
            self._add_child("indented")
            return _LEAF
        return 0

    # -- reference definitions

    def _take_definitions(self, lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
        """``lines`` without the reference definitions that open them; each definition is recorded, first wins."""
        text = "\n".join(content for _, content in lines)
        position = 0
        while position < len(text) and text[position] == "[":
            found = _definition(text, position)
            if found is None:
                break
            label, url, position = found
            self.definitions.setdefault(label, url)
        if position >= len(text):
            return []
        return lines[text[:position].count("\n") :]


def _table_cells(row: str) -> list[str]:
    cells = re.split(r"(?<!\\)\|", row)
    if cells and cells[0] == "":
        cells.pop(0)
    if cells and cells[-1] == "":
        cells.pop()
    return cells


# --- inline content: destinations, titles, labels ---------------------------

_PUNCTUATION = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
_LABEL = re.compile(r"\[((?:[^\[\]\\]|\\.){0,999})\]", re.S)
_ESCAPE_OR_ENTITY = re.compile(
    r"\\([!-/:-@\[-`{-~])|(&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});)"
)


def _unescape(text: str) -> str:
    return _ESCAPE_OR_ENTITY.sub(lambda match: match.group(1) or html.unescape(match.group(2)), text)


def _normal_label(label: str) -> str:
    return " ".join(label.split()).casefold()


def _spaces_and_one_newline(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t":
        index += 1
    if index < len(text) and text[index] == "\n":
        index += 1
        while index < len(text) and text[index] in " \t":
            index += 1
    return index


def _destination(text: str, index: int) -> tuple[str, int] | None:
    """A link destination at ``index``: `<…>`, or a run without spaces whose parentheses balance."""
    if text[index : index + 1] == "<":
        end = index + 1
        while end < len(text) and text[end] not in "<>\n":
            end += 2 if text[end] == "\\" and text[end + 1 : end + 2] in _PUNCTUATION else 1
        if text[end : end + 1] == ">":
            return _unescape(text[index + 1 : end]), end + 1
        return None
    depth, end = 0, index
    while end < len(text):
        char = text[end]
        if char == "\\" and text[end + 1 : end + 2] in _PUNCTUATION:
            end += 2
            continue
        if char.isspace() or ord(char) < 0x20 or char == "\x7f":
            break
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                break
            depth -= 1
        end += 1
    if end == index or depth:
        return None
    return _unescape(text[index:end]), end


def _title_end(text: str, index: int) -> int | None:
    opening = text[index : index + 1]
    if opening not in ("'", '"', "("):
        return None
    closing = ")" if opening == "(" else opening
    end = index + 1
    while end < len(text):
        if text[end] == "\\" and end + 1 < len(text):
            end += 2
            continue
        if text[end] == closing:
            return end + 1
        if opening == "(" and text[end] == "(":
            return None
        end += 1
    return None


def _definition(text: str, index: int) -> tuple[str, str, int] | None:
    """A reference definition at ``index``: (normalised label, URL, where the next line starts)."""
    label = _LABEL.match(text, index)
    if not label or not label.group(1).strip() or text[label.end() : label.end() + 1] != ":":
        return None
    found = _destination(text, _spaces_and_one_newline(text, label.end() + 1))
    if found is None:
        return None
    url, after = found
    end_of_destination = after
    while end_of_destination < len(text) and text[end_of_destination] in " \t":
        end_of_destination += 1
    title_start = _spaces_and_one_newline(text, after)
    if title_start > after:
        title = _title_end(text, title_start)
        if title is not None:
            while title < len(text) and text[title] in " \t":
                title += 1
            if title >= len(text) or text[title] == "\n":
                return _normal_label(label.group(1)), url, title + 1
    if end_of_destination >= len(text) or text[end_of_destination] == "\n":
        return _normal_label(label.group(1)), url, end_of_destination + 1
    return None


_AUTOLINK = re.compile(
    r"<[A-Za-z][A-Za-z0-9.+-]{1,31}:[^<>\x00-\x20]*>"
    r"|<[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*>"
)
_INLINE_HTML = re.compile(
    rf"{_OPEN_TAG}|{_CLOSE_TAG}|<!---?>|<!--(?:[^-]|-[^-]|--[^>])*-->|<[?][\s\S]*?[?]>"
    r"|<![A-Za-z][^>]*>|<!\[CDATA\[[\s\S]*?\]\]>"
)


def _link_target(text: str, index: int, link_text: str, definitions: dict[str, str]) -> tuple[str, int] | None:
    """What a `]` at ``index - 1`` links to — inline, full, collapsed or shortcut reference — and where it ends."""
    if text[index : index + 1] == "(":
        start = _spaces_and_one_newline(text, index + 1)
        if text[start : start + 1] == ")":
            return "", start + 1
        found = _destination(text, start)
        if found is not None:
            url, after = found
            end = _spaces_and_one_newline(text, after)
            if end > after:
                title = _title_end(text, end)
                if title is not None:
                    end = _spaces_and_one_newline(text, title)
            if text[end : end + 1] == ")":
                return url, end + 1
    label = _LABEL.match(text, index)
    if label and label.group(1).strip():
        key, end = _normal_label(label.group(1)), label.end()
    elif label:
        key, end = _normal_label(link_text), label.end()
    else:
        if len(link_text) > 999 or re.search(r"(?<!\\)[\[\]]", link_text):
            return None
        key, end = _normal_label(link_text), index
    return (definitions[key], end) if key in definitions else None


def _inline(text: str, definitions: dict[str, str]) -> tuple[list[tuple[str, int]], str]:
    """The images a run of inline content renders, at their offsets, and the run with what is not HTML masked.

    Read left to right as CommonMark reads it: a backslash escape, a code span,
    an autolink or a raw HTML tag is consumed before any bracket inside it can
    open or close a link. The masked copy has the same length — code spans as
    spaces, escapes as \\x01, a `<` that opens no tag as \\x02 — so a search for
    HTML in it finds only what a browser would read as HTML.
    """
    masked = list(text)
    images: list[tuple[str, int]] = []
    openers: list[list[int]] = []  # [offset, is an image, active]
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and text[index + 1 : index + 2] in _PUNCTUATION:
            masked[index] = masked[index + 1] = "\x01"
            index += 2
        elif char == "`":
            run = len(text) - index - len(text[index:].lstrip("`"))
            close = _closing_backticks(text, index + run, run)
            if close is None:
                index += run
                continue
            for position in range(index, close + run):
                if text[position] != "\n":
                    masked[position] = " "
            index = close + run
        elif char == "<":
            tag = _AUTOLINK.match(text, index) or _INLINE_HTML.match(text, index)
            if tag and tag.re is _INLINE_HTML:
                index = tag.end()
                continue
            masked[index] = "\x02"
            index = tag.end() if tag else index + 1
        elif char == "!" and text[index + 1 : index + 2] == "[":
            openers.append([index, 1, 1])
            index += 2
        elif char == "[":
            openers.append([index, 0, 1])
            index += 1
        elif char == "]" and openers:
            start, is_image, active = openers.pop()
            if not active:
                index += 1
                continue
            body = start + (2 if is_image else 1)
            found = _link_target(text, index + 1, text[body:index], definitions)
            if found is None:
                index += 1
                continue
            url, end = found
            if is_image:
                # An image inside an image's text is alt text: it renders as words, not as an image.
                images = [image for image in images if not start < image[1] < index]
                images.append((url, start))
            else:
                for opener in openers:
                    if not opener[1]:
                        opener[2] = 0  # a link cannot contain a link
            index = end
        else:
            index += 1
    return images, "".join(masked)


def _closing_backticks(text: str, index: int, run: int) -> int | None:
    while (found := text.find("`", index)) != -1:
        length = len(text) - found - len(text[found:].lstrip("`"))
        if length == run:
            return found
        index = found + length
    return None


# --- HTML a browser renders -------------------------------------------------

_HTML_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.S)
_HTML_HEADING = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1\s*>", re.I | re.S)
_HTML_IMAGE_TAG = re.compile(r"<(img|source)\b[^>]*>", re.I)
_HTML_ATTRIBUTE = re.compile(r"""[\s/]([a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?""")
_HTML_PRE = re.compile(r"<pre\b[^>]*>(.*?)(?:</pre\s*>|\Z)", re.I | re.S)


def html_images(fragment: str) -> list[tuple[str, int]]:
    """Each image URL an `<img src>` or an `<img|source srcset>` in ``fragment`` loads, with its offset."""
    found = []
    for tag in _HTML_IMAGE_TAG.finditer(fragment):
        for attribute in _HTML_ATTRIBUTE.finditer(tag.group(0), len(tag.group(1)) + 1):
            name, value = attribute.group(1).lower(), next((g for g in attribute.groups()[1:] if g is not None), "")
            if name == "src" and tag.group(1).lower() == "img":
                found.append((html.unescape(value), tag.start()))
            elif name == "srcset":
                for candidate in html.unescape(value).split(","):
                    if candidate.split():
                        found.append((candidate.split()[0], tag.start()))
    return found


def html_without_comments(fragment: str) -> str:
    """``fragment`` with each HTML comment blanked, line breaks kept: a comment renders nothing."""
    return _HTML_COMMENT.sub(lambda match: re.sub(r"[^\n]", " ", match.group(0)), fragment)


# --- the reading -------------------------------------------------------------


def parse(text: str) -> Structure:
    """The README's headings, images and code, read the way CommonMark (and GitHub's tables) read them."""
    parser = _BlockParser()
    for number, line in enumerate(text.splitlines(), start=1):
        parser.feed(number, line)
    document = parser.finish()

    structure = Structure()
    stream: list[tuple[int, str]] = []  # what a browser receives as HTML, line by line
    for block in _leaves(document):
        if block.kind in ("fence", "indented"):
            structure.code += block.lines
            continue
        if block.kind == "html":
            stream += block.lines
            continue
        if block.kind == "heading":
            text_of = "\n".join(content for _, content in block.lines).strip()
            spelling = "nested" if block.nested() else ("setext" if block.setext else "atx")
            structure.headings.append(Heading(block.level, text_of, block.line, spelling))
        runs = [block.lines] if block.kind != "table" else [[row] for row in block.lines]
        for run in filter(None, runs):
            images, masked = _inline("\n".join(content for _, content in run), parser.definitions)
            line_of = _line_index(run)
            structure.images += [Image(url, line_of(offset)) for url, offset in images]
            stream += [(number, piece) for (number, _), piece in zip(run, masked.split("\n"), strict=True)]

    rendered = html_without_comments("\n".join(content for _, content in stream))
    line_of = _line_index(stream)
    for found in _HTML_HEADING.finditer(rendered):
        inner = re.sub(r"<[^>]+>", "", found.group(2)).strip()
        structure.headings.append(Heading(int(found.group(1)), inner, line_of(found.start()), "html"))
    structure.images += [Image(url, line_of(offset)) for url, offset in html_images(rendered)]
    for found in _HTML_PRE.finditer(rendered):
        offset = found.start(1)
        for piece in found.group(1).split("\n"):
            structure.code.append((line_of(offset), html.unescape(re.sub(r"<[^>]*>", "", piece))))
            offset += len(piece) + 1

    structure.headings.sort(key=lambda heading: heading.line)
    structure.images.sort(key=lambda image: image.line)
    structure.code.sort(key=lambda line: line[0])
    return structure


def _leaves(block: _Block) -> Iterable[_Block]:
    for child in block.children:
        if child.kind in ("document", "blockquote", "item"):
            yield from _leaves(child)
        elif child.kind != "thematic":
            yield child


def _line_index(lines: list[tuple[int, str]]) -> Callable[[int], int]:
    """Map an offset in ``lines`` joined by newlines back to its README line number."""
    starts, offset = [], 0
    for number, text in lines:
        starts.append((offset, number))
        offset += len(text) + 1

    def line_of(position: int) -> int:
        found = 0
        for start, number in starts:
            if start > position:
                break
            found = number
        return found

    return line_of


# --- the standard's own numbers ----------------------------------------------


@dataclass(frozen=True)
class Limits:
    sections: tuple[str, ...]
    max_lines: int
    max_words: int
    max_badges: int
    max_commands: int
    max_description: int


def _number(word: str) -> int:
    word = word.replace(",", "").lower()
    return int(word) if word.isdigit() else _NUMBERS[word]


def limits(standard: str) -> Limits:
    """Every limit the checker enforces, read from the standard's own text."""
    sections = tuple(re.findall(r"^\| \d+ \| `## ([^`]+)` \|", standard, re.M))
    budget = re.search(r"At\s+most\s+([\d,]+)\s+lines\s+and\s+([\d,]+)\s+words", standard)
    rule = standard.find("Badges report checks")
    badges = re.search(r"At\s+most\s+(\w+)\.", standard[rule:]) if rule != -1 else None
    commands = re.search(r"At\s+most\s+(\w+)\s+shell\s+commands", standard)
    description = re.search(r"under\s+(\d+)\s+characters", standard)
    if not (sections and budget and badges and commands and description):
        raise ValueError("the standard no longer states one of its limits in the form this checker reads")
    return Limits(
        sections=sections,
        max_lines=_number(budget.group(1)),
        max_words=_number(budget.group(2)),
        max_badges=_number(badges.group(1)),
        max_commands=_number(commands.group(1)),
        max_description=int(description.group(1)),
    )


def standard_digest(standard: str) -> str:
    return hashlib.sha256(standard.encode("utf-8")).hexdigest()


# --- quick-start commands: read as a shell reads them --------------------------

_OPERATORS = ("&&", "||", ";;", "|&", ";", "|", "&", "(", ")")
_REDIRECTIONS = ("<<<", "<<-", "&>>", "<<", "<>", "<&", ">>", ">&", ">|", "&>", "<", ">")


def _words(line: str) -> list[list[str]]:
    """The simple commands on one logical shell line, as word lists.

    Quotes, backslash escapes and comments as POSIX sh reads them; every
    control operator (`&&`, `||`, `;`, `|`, `&`, parentheses) ends a command;
    a redirection and its target are dropped. An unterminated quote raises
    ValueError. Expansions (`$VAR`, `$(…)`, backticks) are kept literally, so a
    rule that needs a fixed word sees that it is not one.
    """
    commands: list[list[str]] = [[]]
    word: str | None = None
    redirect_target = False
    index = 0

    def end_word() -> None:
        nonlocal word, redirect_target
        if word is not None:
            if not redirect_target:
                commands[-1].append(word)
            word, redirect_target = None, False

    while index < len(line):
        char = line[index]
        if char in " \t\n":
            end_word()
            index += 1
            continue
        if char == "#" and word is None:
            break
        operator = next((op for op in _REDIRECTIONS if line.startswith(op, index)), None)
        if operator:
            if word is not None and word.isdigit():
                word = None  # a file descriptor number
            end_word()
            redirect_target = True
            index += len(operator)
            continue
        operator = next((op for op in _OPERATORS if line.startswith(op, index)), None)
        if operator:
            end_word()
            commands.append([])
            index += len(operator)
            continue
        if char == "'":
            close = line.find("'", index + 1)
            if close == -1:
                raise ValueError("unterminated single quote")
            word = (word or "") + line[index + 1 : close]
            index = close + 1
        elif char == '"':
            index += 1
            text = ""
            while index < len(line) and line[index] != '"':
                if line[index] == "\\" and line[index + 1 : index + 2] in ('"', "\\", "$", "`"):
                    index += 1
                text += line[index]
                index += 1
            if index >= len(line):
                raise ValueError("unterminated double quote")
            word = (word or "") + text
            index += 1
        elif char == "\\":
            word = (word or "") + line[index + 1 : index + 2]
            index += 2
        else:
            word = (word or "") + char
            index += 1
    end_word()
    return [command for command in commands if command]


def _logical_lines(lines: Iterable[str]) -> list[str]:
    """Lines with their backslash-newline continuations joined."""
    joined, pending = [], ""
    for raw in lines:
        trailing = len(raw) - len(raw.rstrip("\\"))
        if trailing % 2:
            pending += raw[:-1] + " "
            continue
        joined.append(pending + raw)
        pending = ""
    if pending:
        joined.append(pending)
    return joined


def commands(section_code: list[str]) -> list[str]:
    """Every command a reader would run, one per simple command, continuations joined.

    A line that does not parse as shell (an unterminated quote) is one command,
    kept as written, and `unpinned()` reports it.
    """
    found: list[str] = []
    for line in _logical_lines(section_code):
        try:
            found += [_join(words) for words in _words(line)]
        except ValueError:
            found.append(line.strip())
    return found


def _join(words: list[str]) -> str:
    return " ".join(
        word if re.fullmatch(r"[\w@%+=:,./-]+", word) else "'" + word.replace("'", "'\\''") + "'" for word in words
    )


# --- quick-start commands: what each runs, and whether it is pinned ----------------


def _always_false(path: str) -> bool:
    return False


@dataclass(frozen=True)
class Repository:
    """What the quick-start rules need to know about the repository whose README they read.

    The defaults are the strictest reading: no clone of a branch, no range,
    no repository script is accepted.
    """

    #: The URL its own README clones it from.
    clone_url: str = ""
    #: The branch whose README the reader is reading — the one CI verifies.
    default_branch: str = ""
    #: Every requirement this repository's CI installs, normalised by `requirement_key`.
    #: The standard accepts a range only when CI installs exactly that range.
    ci_requirements: frozenset[str] = frozenset()
    #: Whether a relative path names a file this repository holds.
    holds: Callable[[str], bool] = _always_false


#: Programs that never come from a Python requirements file, so a quick start
#: that installed one does not vouch for them.
_SYSTEM = frozenset(
    {
        "apk", "apt", "apt-get", "brew", "cargo", "choco", "conda", "curl", "dnf", "docker", "gem", "go",
        "mamba", "micromamba", "nix", "nix-env", "podman", "port", "scoop", "snap", "wget", "winget", "yum",
    }
)  # fmt: skip
#: Commands that run nothing from outside the checkout.
_HARMLESS = frozenset({"cd", "echo", "export", "ls", "make", "mkdir", "printf", "pwd", "true"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish"})

_VERSION = r"\d+(?:\.\d+)*(?:(?:a|b|rc|\.post|\.dev|post|dev)\d+)*(?:[-+][0-9A-Za-z.-]+)?"
_EXACT_VERSION = re.compile(_VERSION)
_EXACT_REF = re.compile(rf"v?{_VERSION}|[0-9a-f]{{40}}")
_REQUIREMENT = re.compile(r"([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(\[[^\]]*\])?\s*(.*?)\s*(?:;.*)?", re.S)
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://|git\+|git@")

_PIP_VALUE_OPTIONS = frozenset(
    {
        "-r", "--requirement", "-c", "--constraint", "-e", "--editable", "-i", "--index-url", "--extra-index-url",
        "-f", "--find-links", "-t", "--target", "--prefix", "--root", "--src", "--platform", "--python-version",
        "--implementation", "--abi", "--upgrade-strategy", "--progress-bar", "--trusted-host", "--cert",
        "--client-cert", "--cache-dir", "--log", "--proxy", "--retries", "--timeout", "--exists-action", "--report",
        "-C", "--config-settings", "--global-option", "--no-binary", "--only-binary", "-p", "--python", "--index",
        "--default-index", "--index-strategy", "--keyring-provider", "--resolution", "--prerelease",
        "--exclude-newer", "--link-mode", "-P", "--upgrade-package", "--reinstall-package", "--overrides",
        "--build-constraints", "--extra", "--group", "--python-platform",
    }
)  # fmt: skip
_TOOL_VALUE_OPTIONS = frozenset(
    {
        "-p", "--python", "--index", "--index-url", "--extra-index-url", "--default-index", "-f", "--find-links",
        "--with-requirements", "--with-editable", "--constraints", "--overrides", "--index-strategy",
        "--python-platform", "--pip-args", "--suffix",
    }
)  # fmt: skip
_UV_RUN_VALUE_OPTIONS = frozenset(
    {
        "-p", "--python", "--package", "--extra", "--group", "--only-group", "--no-group", "--env-file",
        "--directory", "--project", "--index", "--index-url", "--extra-index-url", "--default-index", "-f",
        "--find-links", "--with-requirements", "--with-editable",
    }
)  # fmt: skip
_COPIER_VALUE_OPTIONS = frozenset(
    {"-a", "--answers-file", "-d", "--data", "--data-file", "-x", "--exclude", "-s", "--skip", "--conflict",
     "-c", "--context-lines", "-r", "--vcs-ref"}
)  # fmt: skip
_GIT_CLONE_VALUE_OPTIONS = frozenset(
    {
        "-b", "--branch", "--revision", "-o", "--origin", "-u", "--upload-pack", "--reference", "--reference-if-able",
        "--separate-git-dir", "--depth", "--shallow-since", "--shallow-exclude", "-c", "--config", "-j", "--jobs",
        "--filter", "--template", "--server-option", "--bundle-uri",
    }
)  # fmt: skip


def requirement_key(spec: str) -> str:
    """A requirement as `Repository.ci_requirements` stores it: no whitespace, lower case."""
    return re.sub(r"\s+", "", spec).lower()


def _options(words: list[str], takes_value: frozenset[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """(option, value) pairs and the positional words, reading `--opt value`, `--opt=value` and `-xVALUE`."""
    options: list[tuple[str, str]] = []
    positional: list[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        if word == "--":
            positional += words[index + 1 :]
            break
        if word.startswith("-") and len(word) > 1:
            name, equals, value = word.partition("=")
            if name in takes_value and not equals:
                if name.startswith("--") or len(name) == 2:
                    index += 1
                    value = words[index] if index < len(words) else ""
            elif not equals and not word.startswith("--") and word[:2] in takes_value:
                name, value = word[:2], word[2:]
            options.append((name, value))
        else:
            positional.append(word)
        index += 1
    return options, positional


def _exact_reference(url: str) -> bool:
    """A VCS URL fixed at a tag or a commit (`…@v1.2.3`, `…@<40 hex>`), or an archive with its hash."""
    if "#sha256=" in url:
        return True
    ref = url.rsplit("@", 1)[1].split("#", 1)[0] if "@" in url.split("://", 1)[-1] else ""
    return bool(ref) and _EXACT_REF.fullmatch(ref) is not None


def _requirement(spec: str, repository: Repository) -> tuple[str | None, str]:
    """(why ``spec`` is not pinned or None, the package name it provides)."""
    if spec in (".", "..") or spec.startswith(("./", "../", "/", "~")):
        return None, ""
    if _URL.match(spec):
        return (None if _exact_reference(spec) else f"installs {spec!r} at no fixed ref or hash"), ""
    match = _REQUIREMENT.fullmatch(spec)
    if not match:
        return f"installs {spec!r}, which does not read as a requirement", ""
    name, constraint = match.group(1), match.group(3)
    if not constraint:
        return f"installs {name!r} without a version", name.lower()
    if constraint.startswith("@"):
        url = constraint[1:].strip()
        return (None if _exact_reference(url) else f"installs {name!r} from {url!r} at no fixed ref or hash"), ""
    if re.fullmatch(rf"===?\s*{_VERSION}", constraint):
        return None, name.lower()
    if requirement_key(spec) in repository.ci_requirements:
        return None, name.lower()
    return (
        f"installs {name!r} at {constraint!r}, a range; pin it with `==`, or bound it exactly as this "
        f"repository's CI installs it",
        name.lower(),
    )


def _exact_tool(spec: str) -> bool:
    if _URL.match(spec):
        return _exact_reference(spec)
    name, at, version = spec.rpartition("@") if "@" in spec.lstrip("@") else ("", "", "")
    if at and name:
        return _EXACT_VERSION.fullmatch(version) is not None
    match = _REQUIREMENT.fullmatch(spec)
    return match is not None and re.fullmatch(rf"===?\s*{_VERSION}", match.group(3) or "") is not None


def _same_repository(url: str, clone_url: str) -> bool:
    def key(text: str) -> str:
        text = re.sub(r"^git@([^:]+):", r"https://\1/", text.strip().lower())
        return re.sub(r"(?:\.git)?/*$", "", text)

    return bool(clone_url) and key(url) == key(clone_url)


@dataclass
class _Reading:
    repository: Repository
    #: Package names an earlier command installed pinned; "*" once a pinned
    #: requirements file or lock was installed into the active environment.
    provided: set[str] = field(default_factory=set)


def unpinned(command: str, repository: Repository | None = None, provided: Iterable[str] = ()) -> str | None:
    """Why a quick-start command runs something unpinned, or None when everything it runs is pinned.

    ``provided`` names what earlier commands installed pinned; `unpinned_commands`
    threads it through a whole quick start.
    """
    reading = _Reading(repository or Repository(), set(provided))
    return _judge_line(command, reading)


def unpinned_commands(run: list[str], repository: Repository | None = None) -> list[str]:
    """One reason per quick-start command that runs something unpinned, in order."""
    reading = _Reading(repository or Repository())
    return [reason for command in run if (reason := _judge_line(command, reading))]


def _judge_line(command: str, reading: _Reading) -> str | None:
    try:
        parsed = _words(command)
    except ValueError:
        return f"`{command}` does not parse as a shell command"
    for words in parsed:
        reason = _judge(words, reading, locked=False)
        if reason:
            return f"`{command}` {reason}"
    return None


def _program(word: str) -> str:
    base = word.rsplit("/", 1)[-1]
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?|py", base):
        return "python"
    if re.fullmatch(r"pip(?:\d+(?:\.\d+)*)?(?:\.exe)?", base):
        return "pip"
    return base


def _strip_prefixes(words: list[str]) -> list[str]:
    """``words`` without leading assignments and the wrappers that run the rest as a command."""
    while words:
        head = words[0]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", head, re.S):
            words = words[1:]
        elif head in ("sudo", "doas", "env", "command", "exec", "time", "nohup", "nice"):
            words = words[1:]
            while words and words[0].startswith("-"):
                takes_value = words[0] in ("-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-n", "-S")
                words = words[2:] if takes_value else words[1:]
        else:
            return words
    return words


def _judge(words: list[str], reading: _Reading, *, locked: bool) -> str | None:
    """Why this simple command runs something unpinned; ``locked`` when it runs inside `uv run`."""
    words = _strip_prefixes(words)
    if not words:
        return None
    if any("$(" in word or "`" in word for word in words):
        return "runs a command substitution, which the standard cannot read"
    head, args = _program(words[0]), words[1:]

    if head in _HARMLESS:
        return None
    if head in _SHELLS or head in ("source", "."):
        return _shell(head, args, reading, locked=locked)
    if head == "python":
        return _python(args, reading, locked=locked)
    if head == "pip":
        return _pip(args, reading)
    if head == "uv":
        return _uv(args, reading)
    if head in ("uvx", "pipx"):
        return _tool(args if head == "uvx" else args[1:], reading, sub=None if head == "uvx" else args[:1])
    if head == "npx":
        return _npx(args)
    if head == "npm":
        return _npm(args)
    if head == "git":
        return _git(args, reading.repository)
    if head == "copier":
        if not (locked or {"copier", "*"} & reading.provided):
            return "runs whichever copier is on PATH; install it pinned first, or run `uv run copier`"
        return _copier(args)
    if head in _SYSTEM:
        return f"runs `{head}`, which no pin rule of the standard reads; a quick start runs pinned tools only"
    if "/" in words[0]:
        return None if reading.repository.holds(words[0]) else f"runs {words[0]!r}, which this repository does not hold"
    if locked or {head, "*"} & reading.provided:
        return None
    return f"runs `{head}` from whatever is on PATH: no earlier quick-start command installed it pinned"


def _shell(head: str, args: list[str], reading: _Reading, *, locked: bool) -> str | None:
    options, positional = _options(args, frozenset({"-c", "-o", "-O", "+O"}))
    for name, value in options:
        if name == "-c":
            for words in _words(value):
                reason = _judge(words, reading, locked=locked)
                if reason:
                    return reason
            return None
    if head in _SHELLS and (not positional or "-s" in dict(options)):
        return "executes whatever is piped into it"
    script = positional[0] if positional else ""
    return None if reading.repository.holds(script) else f"runs {script!r}, which this repository does not hold"


def _python(args: list[str], reading: _Reading, *, locked: bool) -> str | None:
    index = 0
    while index < len(args):
        word = args[index]
        if word == "-m" or (word.startswith("-m") and len(word) > 2):
            module = args[index + 1] if word == "-m" and index + 1 < len(args) else word[2:]
            rest = args[index + 2 :] if word == "-m" else args[index + 1 :]
            if module == "pip":
                return _pip(rest, reading)
            if module in ("venv", "ensurepip"):
                return None
            if locked or {module.lower(), "*"} & reading.provided:
                return None
            return f"runs the module {module!r} from whatever is installed: no earlier command installed it pinned"
        if word == "-c" or word.startswith("-c"):
            return "runs inline code, which the standard cannot read"
        if word in ("-X", "-W"):
            index += 2
            continue
        if word.startswith("-"):
            index += 1
            continue
        return None if reading.repository.holds(word) else f"runs {word!r}, which this repository does not hold"
    return "executes whatever is piped into it"


def _pip(args: list[str], reading: _Reading) -> str | None:
    _, positional = _options(args, _PIP_VALUE_OPTIONS)
    subcommand = positional[0] if positional else ""
    if subcommand in ("list", "show", "freeze", "check", "help") or args[:1] == ["--version"]:
        return None
    if subcommand != "install":
        return f"runs `pip {subcommand}`, which no pin rule of the standard reads"
    return _pip_install(args[args.index("install") + 1 :], reading)


def _pip_install(args: list[str], reading: _Reading) -> str | None:
    options, positional = _options(args, _PIP_VALUE_OPTIONS)
    names: list[str] = []
    for name, value in options:
        if name in ("-r", "--requirement"):
            if _URL.match(value):
                return f"installs the remote requirements file {value!r}"
            names.append("*")
        elif name in ("-e", "--editable"):
            reason, _ = _requirement(value, reading.repository)
            if reason:
                return reason
    for spec in positional:
        reason, package = _requirement(spec, reading.repository)
        if reason:
            return reason
        names.append(package)
    reading.provided.update(name for name in names if name)
    return None


def _uv(args: list[str], reading: _Reading) -> str | None:
    _, positional = _options(args, frozenset({"--directory", "--project", "--config-file", "--cache-dir"}))
    subcommand = positional[0] if positional else ""
    rest = args[args.index(subcommand) + 1 :] if subcommand else []
    if subcommand == "pip":
        _, inner = _options(rest, _PIP_VALUE_OPTIONS)
        if inner[:1] == ["install"]:
            return _pip_install(rest[rest.index("install") + 1 :], reading)
        if inner[:1] == ["sync"]:
            reading.provided.add("*")
            return None
        if inner[:1] in (["list"], ["show"], ["freeze"], ["check"]):
            return None
        return f"runs `uv pip {' '.join(inner[:1])}`, which no pin rule of the standard reads"
    if subcommand == "sync":
        moving = {"--upgrade", "-U", "--upgrade-package", "-P"} & {word.split("=", 1)[0] for word in rest}
        return "moves the lock it installs from" if moving else None
    if subcommand == "run":
        return _uv_run(rest, reading)
    if subcommand == "tool":
        return _tool(rest[1:], reading, sub=rest[:1])
    return f"runs `uv {subcommand}`, which no pin rule of the standard reads"


def _uv_run(args: list[str], reading: _Reading) -> str | None:
    index, locked = 0, True
    while index < len(args) and args[index].startswith("-"):
        name, equals, value = args[index].partition("=")
        if name in ("--with",) or name in _UV_RUN_VALUE_OPTIONS:
            if not equals:
                index += 1
                value = args[index] if index < len(args) else ""
            if name == "--with":
                for spec in value.split(","):
                    if not _exact_tool(spec.strip()):
                        return f"adds {spec.strip()!r} without an exact version"
        elif name == "--no-project":
            locked = False
        elif name in ("--upgrade", "-U", "--upgrade-package", "-P"):
            return "moves the lock it runs from"
        index += 1
    inner = args[index:]
    return _judge(inner, reading, locked=locked) if inner else None


def _tool(args: list[str], reading: _Reading, sub: list[str] | None) -> str | None:
    """`uvx`, `uv tool run|install`, `pipx run|install`: what they fetch must be an exact version."""
    if sub is not None and sub not in (["run"], ["install"]):
        return f"runs `{' '.join(sub) or '(nothing)'}`, which no pin rule of the standard reads"
    source = None
    index, tool = 0, ""
    while index < len(args):
        name, equals, value = args[index].partition("=")
        if name in ("--from", "--spec", "--with") or name in _TOOL_VALUE_OPTIONS:
            if not equals:
                index += 1
                value = args[index] if index < len(args) else ""
            if name in ("--from", "--spec"):
                source = value
            elif name == "--with":
                for spec in value.split(","):
                    if not _exact_tool(spec.strip()):
                        return f"adds {spec.strip()!r} without an exact version"
        elif not args[index].startswith("-"):
            tool = args[index]
            break
        index += 1
    spec = source or tool
    if not _exact_tool(spec):
        return f"{'installs' if sub == ['install'] else 'runs'} {spec!r} without an exact version"
    if sub == ["install"]:
        reading.provided.add(re.split(r"[=@<>~!\[ ]", spec, maxsplit=1)[0].lower())
    return None


def _npx(args: list[str]) -> str | None:
    options, positional = _options(args, frozenset({"-p", "--package", "-c", "--call"}))
    if any(name in ("-c", "--call") for name, _ in options):
        return "runs inline code, which the standard cannot read"
    packages = [value for name, value in options if name in ("-p", "--package")] or positional[:1]
    for package in packages:
        _, at, version = package.rpartition("@") if "@" in package.lstrip("@") else (package, "", "")
        if not at or not _EXACT_VERSION.fullmatch(version):
            return f"runs {package!r} without an exact version"
    return None


def _npm(args: list[str]) -> str | None:
    _, positional = _options(args, frozenset({"--prefix", "-w", "--workspace"}))
    subcommand, specs = (positional[0], positional[1:]) if positional else ("", [])
    if subcommand in ("ci", "run", "run-script", "test", "start"):
        return None
    if subcommand in ("install", "i", "add", "isntall"):
        if not specs:
            return "may rewrite package-lock.json; `npm ci` installs exactly the lock"
        for spec in specs:
            _, at, version = spec.rpartition("@") if "@" in spec.lstrip("@") else (spec, "", "")
            if not at or not _EXACT_VERSION.fullmatch(version):
                return f"installs {spec!r} without an exact version"
        return None
    return f"runs `npm {subcommand}`, which no pin rule of the standard reads"


def _git(args: list[str], repository: Repository) -> str | None:
    _, positional = _options(args, frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"}))
    subcommand = positional[0] if positional else ""
    if subcommand != "clone":
        return f"runs `git {subcommand}`, which no pin rule of the standard reads"
    options, positional = _options(args[args.index("clone") + 1 :], _GIT_CLONE_VALUE_OPTIONS)
    refs = [value for name, value in options if name in ("-b", "--branch", "--revision")]
    if not refs:
        return "clones without naming the ref it means"
    ref, url = refs[-1], positional[0] if positional else ""
    if _EXACT_REF.fullmatch(ref):
        return None
    if ref == repository.default_branch and _same_repository(url, repository.clone_url):
        return None  # this repository, at the branch whose README the reader is reading
    return f"clones {ref!r}, a ref that moves; name a release tag or a commit"


def _copier(args: list[str]) -> str | None:
    options, positional = _options(args, _COPIER_VALUE_OPTIONS)
    subcommand = positional[0] if positional else ""
    if subcommand not in ("copy", "update", "recopy"):
        return None
    refs = [value for name, value in options if name in ("-r", "--vcs-ref")]
    if not refs:
        return "renders whichever tag sorts highest"
    ref = refs[-1]
    if _EXACT_REF.fullmatch(ref):
        return None
    source = positional[1] if len(positional) > 1 else ""
    local = bool(source) and not (_URL.match(source) or re.match(r"g[hl]:", source))
    if ref == "HEAD" and subcommand == "copy" and local:
        return None  # the template in this checkout, as it stands
    return f"renders {ref!r}, a ref that moves; name a release tag"


def requirements_installed_by(workflows: Iterable[str]) -> frozenset[str]:
    """Every requirement a `pip install` in these workflow files names, as `Repository.ci_requirements` stores it."""
    found: set[str] = set()
    for text in workflows:
        lines = [re.sub(r"^\s*(?:-\s+)?(?:run:\s*[|>]?-?\s*)?", "", line) for line in text.splitlines()]
        for line in _logical_lines(lines):
            try:
                parsed = _words(line)
            except ValueError:
                continue
            for words in parsed:
                words = _strip_prefixes(words)
                if not words:
                    continue
                head, args = _program(words[0]), words[1:]
                if head == "python" and args[:2] == ["-m", "pip"]:
                    head, args = "pip", args[2:]
                elif head == "uv" and args[:1] == ["pip"]:
                    head, args = "pip", args[1:]
                if head != "pip" or "install" not in args:
                    continue
                _, positional = _options(args[args.index("install") + 1 :], _PIP_VALUE_OPTIONS)
                found.update(requirement_key(spec) for spec in positional)
    return frozenset(found)


# --- the checks ---------------------------------------------------------------


def _between(text: str, begin: str, end: str) -> str | None:
    start = text.find(begin)
    stop = text.find(end, start + len(begin)) if start != -1 else -1
    if start == -1 or stop == -1:
        return None
    return text[start + len(begin) : stop].strip("\n")


def _replace(text: str, begin: str, end: str, body: str) -> str:
    start, stop = text.find(begin), text.find(end)
    if start == -1 or stop == -1 or stop < start:
        raise ValueError(f"the README has no `{begin}` … `{end}` block to regenerate")
    return text[: start + len(begin)] + "\n" + body + "\n" + text[stop:]


def write(readme: str, generated: str, standard: str) -> str:
    """The README with its status block and its related-repositories table regenerated."""
    related = _between(standard, RELATED_BEGIN, RELATED_END)
    if related is None:
        raise ValueError("the standard no longer carries its related-repositories table")
    return _replace(_replace(readme, STATUS_BEGIN, STATUS_END, generated), RELATED_BEGIN, RELATED_END, related)


def check(
    readme: str,
    standard: str,
    generated: str,
    gated_badges: tuple[re.Pattern[str], ...] = (),
    expected_digest: str = STANDARD_SHA256,
    repository: Repository | None = None,
) -> list[str]:
    """One message per departure from the standard. Empty means the README conforms."""
    failures: list[str] = []
    digest = standard_digest(standard)
    if digest != expected_digest:
        failures.append(
            f"docs/governance/readme-standard.md has SHA-256 {digest}, not the {expected_digest} both "
            f"repositories pin. The standard is shared byte for byte: change it in both, then update "
            f"STANDARD_SHA256 in both copies of scripts/readme_standard.py"
        )
    try:
        bounds = limits(standard)
    except ValueError as unreadable:
        return [*failures, str(unreadable)]

    lines = readme.splitlines()
    structure = parse(readme)

    if not lines or not re.match(r"^# \S", lines[0]):
        failures.append("the README does not open with its `# <repository name>` title")
    description = next((line for line in lines[1:] if line.strip()), "")
    if not description.startswith("> "):
        failures.append("the title is not followed by a one-line `> ` description")
    elif len(description) - 2 > bounds.max_description:
        failures.append(
            f"the description is {len(description) - 2} characters; the standard allows {bounds.max_description}"
        )

    titles = [heading for heading in structure.headings if heading.level == 1]
    if len(titles) != 1:
        failures.append(f"{len(titles)} level-1 headings; the standard allows only the title")
    sections = [heading for heading in structure.headings if heading.level == 2]
    odd = [f"{h.text!r} ({h.spelling}, line {h.line})" for h in sections if h.spelling != "atx"]
    if odd:
        failures.append(f"level-2 headings in a spelling the standard does not use: {odd}")
    found = tuple(heading.text for heading in sections)
    if found != bounds.sections:
        missing = [name for name in bounds.sections if name not in found]
        extra = [name for name in found if name not in bounds.sections]
        detail = []
        if missing:
            detail.append(f"missing {missing}")
        if extra:
            detail.append(f"not in the standard {extra}")
        if not missing and not extra:
            detail.append(f"out of order: {list(found)}")
        failures.append("level-2 sections differ from the standard: " + "; ".join(detail))

    block = _between(readme, STATUS_BEGIN, STATUS_END)
    if block is None:
        failures.append(f"no generated status block between `{STATUS_BEGIN}` and `{STATUS_END}`")
    elif block.strip() != generated.strip():
        failures.append("the status block is stale; regenerate it with scripts/check_readme.py --write")

    canonical = _between(standard, RELATED_BEGIN, RELATED_END)
    carried = _between(readme, RELATED_BEGIN, RELATED_END)
    if canonical is None:
        failures.append("the standard no longer carries its related-repositories table")
    elif carried is None or carried.strip() != canonical.strip():
        failures.append("the related-repositories table differs from the one in docs/governance/readme-standard.md")

    first_section = sections[0].line if sections else len(lines) + 1
    allowed = (*ALLOWED_BADGES, *gated_badges)
    title_badges = [image for image in structure.images if image.line < first_section]
    for image in title_badges:
        if not any(pattern.fullmatch(image.url) for pattern in allowed):
            failures.append(f"badge {image.url} states a claim rather than reporting a check")
    if len(title_badges) > bounds.max_badges:
        failures.append(f"{len(title_badges)} badges; the standard allows {bounds.max_badges}")
    for image in structure.images:
        if image.line >= first_section and _BADGE_HOSTS.search(image.url):
            failures.append(f"badge {image.url} at line {image.line} sits outside the title area")

    quick = next((h for h in sections if h.text == "Quick start"), None)
    if quick is not None:
        after = [h.line for h in structure.headings if h.line > quick.line and h.level <= 2]
        end = after[0] if after else len(lines) + 1
        run = commands([text for number, text in structure.code if quick.line < number < end])
        if len(run) > bounds.max_commands:
            failures.append(f"the quick start runs {len(run)} commands; the standard allows {bounds.max_commands}")
        for reason in unpinned_commands(run, repository):
            failures.append(f"quick start: {reason}")

    if len(lines) > bounds.max_lines:
        failures.append(f"{len(lines)} lines; the standard's budget is {bounds.max_lines}")
    words = len(readme.split())
    if words > bounds.max_words:
        failures.append(f"{words} words; the standard's budget is {bounds.max_words}")
    return failures
