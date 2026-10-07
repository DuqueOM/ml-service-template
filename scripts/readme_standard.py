"""The shared README standard, as code: parse a README's structure and check it against the standard.

This module is byte-identical in ml-service-template and ml-platform, like
`docs/governance/readme-standard.md` itself; each repository's
`scripts/check_readme.py` imports it and adds only what is that repository's
own — the status block's sources and any badge a local gate holds. It is
standard-library only, because ml-service-template runs its gates on a bare
`python3`.

**Why a parser and not line patterns.** The first checker counted `## ` lines,
saw badges only when linked and placed under the title, and counted commands
one per line inside a fence. QA-4 round seventeen passed it with a setext
heading, an indented `   ## `, an HTML `<h2>`, an unlinked claim badge, a
badge inside a section, twelve commands in an indented block and twelve chained
on one fenced line. Markdown has more than one spelling for each of those
things, and a reader's renderer honours all of them, so this reads the
structure the way CommonMark does — closely enough that ml-platform's tests
compare it with markdown-it-py on the README and on an adversarial corpus.

**Why the limits are read from the standard.** They were constants here, so the
standard's text could change with every gate green. They are parsed from the
standard now, and the standard itself is pinned by its SHA-256
(`STANDARD_SHA256`), identical in both repositories: changing it is changing
this constant in both, in the same session, which is what "shared byte for
byte" means.
"""

from __future__ import annotations

import hashlib
import re
import shlex
from dataclasses import dataclass, field

#: The SHA-256 of `docs/governance/readme-standard.md`, the same in both
#: repositories. A change to the standard is a change to this line in both.
STANDARD_SHA256 = "c3507c35cd4c57dc055276d487fa8c3aa8173cb6f61b994fe5582bd56a865d98"

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
    #: (line number, text) of every line inside a fenced or indented code block.
    code: list[tuple[int, str]] = field(default_factory=list)


_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_THEMATIC = re.compile(r"^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")
_CONTAINER = re.compile(r"^ {0,3}(?:>[ \t]?|(?:[-*+]|\d{1,9}[.)])[ \t]+)")
_LIST_ITEM = re.compile(r"^ {0,3}(?:[-*+]|\d{1,9}[.)])[ \t]+\S")
_HTML_HEADING = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1\s*>", re.I | re.S)
_INLINE_IMAGE = re.compile(r"!\[(?:[^\]\\]|\\.)*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'(][^)]*)?\)")
_REFERENCE_IMAGE = re.compile(r"!\[((?:[^\]\\]|\\.)*)\](?:\[((?:[^\]\\]|\\.)*)\])?")
_DEFINITION = re.compile(r"^ {0,3}\[((?:[^\]\\]|\\.)+)\]:[ \t]*<?(\S+?)>?(?:[ \t]+.*)?$")
_HTML_IMAGE = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']?([^\"'\s>]+)", re.I)


def _strip_containers(line: str) -> tuple[str, bool]:
    """A line with its blockquote markers and list-item markers removed, and whether there were any."""
    stripped, nested = line, False
    while True:
        match = _CONTAINER.match(stripped)
        if not match:
            return stripped, nested
        stripped, nested = stripped[match.end() :], True


def parse(text: str) -> Structure:
    """The README's headings, images and code lines, read the way CommonMark reads them."""
    structure = Structure()
    lines = text.splitlines()
    fence: tuple[str, int] | None = None
    previous_blank = True
    previous_paragraph: str | None = None
    previous_indented_code = False
    in_list = False
    prose: list[tuple[int, str]] = []

    for number, line in enumerate(lines, start=1):
        if fence is not None:
            closing = re.match(rf"^ {{0,3}}{re.escape(fence[0][0])}{{{fence[1]},}}[ \t]*$", line)
            if closing:
                fence = None
            else:
                structure.code.append((number, line))
            previous_blank, previous_paragraph = False, None
            continue

        opening = _FENCE_OPEN.match(line)
        if opening and not (opening.group(1)[0] == "`" and "`" in opening.group(2)):
            fence = (opening.group(1), len(opening.group(1)))
            previous_blank, previous_paragraph, previous_indented_code = False, None, False
            continue

        blank = not line.strip()
        indented = re.match(r"^(?: {4}|\t)", line) is not None
        if (
            indented
            and not blank
            and previous_paragraph is None
            and not in_list
            and (previous_blank or previous_indented_code)
        ):
            structure.code.append((number, line))
            previous_indented_code, previous_blank = True, False
            continue
        if blank:
            previous_blank, previous_paragraph, previous_indented_code = True, None, False
            continue
        previous_indented_code = False

        if _LIST_ITEM.match(line):
            in_list = True
        elif previous_blank and not indented:
            in_list = False

        setext = _SETEXT.match(line)
        if setext and previous_paragraph is not None:
            level = 1 if setext.group(1)[0] == "=" else 2
            structure.headings.append(Heading(level, previous_paragraph.strip(), number - 1, "setext"))
            previous_blank, previous_paragraph = False, None
            continue

        content, nested = _strip_containers(line)
        atx = _ATX.match(content)
        if atx:
            level = len(atx.group(1))
            spelling = "nested" if nested else "atx"
            structure.headings.append(Heading(level, (atx.group(2) or "").strip(), number, spelling))
            previous_blank, previous_paragraph = False, None
            continue
        if _THEMATIC.match(line):
            previous_blank, previous_paragraph = False, None
            continue

        prose.append((number, line))
        # A setext underline turns only a PARAGRAPH line into a heading: not a
        # list item, not a quoted line, not a table row's delimiter.
        previous_paragraph = None if nested or _LIST_ITEM.match(line) else line
        previous_blank = False

    prose_text = "\n".join(text for _, text in prose)
    line_of = _line_index(prose)
    for found in _HTML_HEADING.finditer(prose_text):
        inner = re.sub(r"<[^>]+>", "", found.group(2)).strip()
        structure.headings.append(Heading(int(found.group(1)), inner, line_of(found.start()), "html"))
    structure.headings.sort(key=lambda heading: heading.line)

    definitions = {
        match.group(1).strip().lower(): match.group(2) for _, line in prose if (match := _DEFINITION.match(line))
    }
    for match in _INLINE_IMAGE.finditer(prose_text):
        structure.images.append(Image(match.group(1), line_of(match.start())))
    inline_spans = [match.span() for match in _INLINE_IMAGE.finditer(prose_text)]
    for match in _REFERENCE_IMAGE.finditer(prose_text):
        if any(start <= match.start() < end for start, end in inline_spans):
            continue
        label = (match.group(2) or match.group(1)).strip().lower()
        if label in definitions:
            structure.images.append(Image(definitions[label], line_of(match.start())))
    for match in _HTML_IMAGE.finditer(prose_text):
        structure.images.append(Image(match.group(1), line_of(match.start())))
    structure.images.sort(key=lambda image: image.line)
    return structure


def _line_index(prose: list[tuple[int, str]]):  # type: ignore[no-untyped-def]
    """Map an offset in the joined prose back to its README line number."""
    starts, offset = [], 0
    for number, text in prose:
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


# --- quick-start commands -----------------------------------------------------

_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\|)\s*")
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}


def commands(section_code: list[str]) -> list[str]:
    """Every command a reader would run: one per segment of `&&`, `||`, `;` and `|`, continuations joined."""
    joined: list[str] = []
    pending = ""
    for raw in section_code:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"\s+#\s.*$", "", line)
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        joined.append(pending + line)
        pending = ""
    if pending:
        joined.append(pending)
    return [segment for line in joined for segment in _segments(line) if segment]


def _segments(line: str) -> list[str]:
    """Split on shell operators outside quotes."""
    segments, current, quote = [], "", ""
    i = 0
    while i < len(line):
        char = line[i]
        if quote:
            current += char
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote, current = char, current + char
        elif line.startswith(("&&", "||"), i):
            segments.append(current.strip())
            current, i = "", i + 2
            continue
        elif char in ";|":
            segments.append(current.strip())
            current = ""
        else:
            current += char
        i += 1
    segments.append(current.strip())
    return segments


def unpinned(command: str) -> str | None:
    """Why a quick-start command runs an unpinned tool, or None when it pins what it installs."""
    try:
        words = shlex.split(command)
    except ValueError:
        return f"`{command}` does not parse as a shell command"
    if not words:
        return None
    if words[0] in _SHELLS and len(words) == 1:
        return f"`{command}` executes whatever is piped into it"
    head = words[0]
    if head in {"python", "python3"} and words[1:3] == ["-m", "pip"]:
        words, head = words[2:], "pip"
    if head == "uv" and words[1:2] == ["pip"]:
        words, head = words[1:], "pip"
    if head in {"pip", "pip3"} and "install" in words:
        rest = words[words.index("install") + 1 :]
        skip_next = False
        for word in rest:
            if skip_next:
                skip_next = False
                continue
            if word in {"-r", "--requirement", "-c", "--constraint", "-e", "--editable"}:
                skip_next = True
                continue
            if word.startswith("-") or word in {".", ".."} or word.startswith(("./", "/")):
                continue
            if not re.search(r"==|~=|>=|<=|!=|<|>|@", word):
                return f"`{command}` installs {word!r} without a version"
        return None
    if head in {"uvx", "pipx"} or words[:3] == ["uv", "tool", "run"]:
        args = words[3:] if head == "uv" else words[1:]
        if args[:1] in (["run"], ["install"]) and head == "pipx":
            args = args[1:]
        if "--from" in args:
            source = args[args.index("--from") + 1] if args.index("--from") + 1 < len(args) else ""
            return None if re.search(r"==|@", source) else f"`{command}` runs {source!r} without a version"
        tool = next((word for word in args if not word.startswith("-")), "")
        return None if re.search(r"@|==", tool) else f"`{command}` runs {tool!r} without a version"
    if head == "npx":
        tool = next((word for word in words[1:] if not word.startswith("-")), "")
        return None if "@" in tool.lstrip("@") else f"`{command}` runs {tool!r} without a version"
    if words[:2] == ["git", "clone"] and not ({"--branch", "-b", "--revision"} & set(words)):
        return f"`{command}` clones without naming the ref it means"
    renders = "copier" in words and bool({"copy", "update"} & set(words))
    if renders and "--vcs-ref" not in words and not any(w.startswith("--vcs-ref=") for w in words):
        return f"`{command}` renders whichever tag sorts highest"
    return None


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
        for command in run:
            reason = unpinned(command)
            if reason:
                failures.append(f"quick start: {reason}")

    if len(lines) > bounds.max_lines:
        failures.append(f"{len(lines)} lines; the standard's budget is {bounds.max_lines}")
    words = len(readme.split())
    if words > bounds.max_words:
        failures.append(f"{words} words; the standard's budget is {bounds.max_words}")
    return failures
