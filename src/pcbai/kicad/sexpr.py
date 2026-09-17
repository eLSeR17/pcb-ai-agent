"""Minimal, dependency-free S-expression parser for KiCad files.

KiCad EDA (6.x and newer) stores netlists, schematics and boards as
S-expressions. This module implements just enough of the syntax to read
them with precise error reporting, with no third-party dependencies.

Documented grammar::

    sexpr   := list | atom
    list    := '(' (sexpr)* ')'
    atom    := string | symbol | number
    string  := '"' (escape | any-char)* '"'
    number  := int | float

Tokenisation rules:

- Atoms are tokens terminated by whitespace, parentheses or a double quote.
- A token matching an integer or floating-point number (including exponents
  such as ``1e3``) is converted to ``int``/``float``; every other token is
  returned as a symbol (``str``).
- Strings honour backslash escapes (``\\\\``, ``\\"``, ``\\n``, ``\\t``,
  ``\\r``). Unknown escapes keep the escaped character literally, matching
  KiCad's own lenient reader.
- Whitespace (space, tab, CR, LF) separates forms. ``;`` comments are not
  part of KiCad's S-expression format and are therefore not supported.

Malformed input raises :class:`SExprError` (a :class:`ValueError`) carrying
the 1-based line and column of the offending character or form start.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["SExprError", "parse", "parse_file"]

_EOF = "\0"
_WHITESPACE = " \t\r\n"
_ATOM_STOPPERS = _WHITESPACE + '()"'

_INT_RE = re.compile(r"[+-]?\d+")
_FLOAT_RE = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")


class SExprError(ValueError):
    """S-expression syntax error with the exact source position.

    Attributes:
        message: Human-readable description of the problem.
        line: 1-based line of the offending character (or form start).
        column: 1-based column of the offending character (or form start).
    """

    def __init__(self, message: str, line: int, column: int) -> None:
        super().__init__(f"line {line}, column {column}: {message}")
        self.message = message
        self.line = line
        self.column = column


class _Reader:
    """Character cursor over the input with 1-based line/column tracking."""

    __slots__ = ("text", "pos", "line", "col")

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.line = 1
        self.col = 1

    def at_end(self) -> bool:
        return self.pos >= len(self.text)

    def peek(self) -> str:
        return _EOF if self.at_end() else self.text[self.pos]

    def advance(self) -> str:
        """Consume and return the next character (or ``_EOF`` at the end)."""
        if self.at_end():
            return _EOF
        char = self.text[self.pos]
        self.pos += 1
        if char == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return char


def parse(text: str) -> list:
    """Parse a single top-level S-expression and return it as a Python list.

    Exactly one top-level form is expected and it must be a list; KiCad
    files always start with a parenthesised form such as ``(export ...)``
    or ``(kicad_sch ...)``.
    """
    reader = _Reader(text)
    _skip_whitespace(reader)
    if reader.at_end():
        raise SExprError("empty input: expected an S-expression", 1, 1)
    char = reader.peek()
    if char != "(":
        if char == ")":
            raise SExprError("unmatched ')' at top level", reader.line, reader.col)
        raise SExprError("expected '(' to start the top-level form", reader.line, reader.col)
    form = _read_list(reader)
    _skip_whitespace(reader)
    if not reader.at_end():
        raise SExprError(
            f"trailing content after the top-level form (unexpected {reader.peek()!r})",
            reader.line,
            reader.col,
        )
    return form


def parse_file(path: str | Path) -> list:
    """Parse the UTF-8 contents of ``path`` as a single S-expression."""
    return parse(Path(path).read_text(encoding="utf-8"))


def _skip_whitespace(reader: _Reader) -> None:
    while reader.peek() in _WHITESPACE:
        reader.advance()


def _read_list(reader: _Reader) -> list:
    """Parse the list whose opening ``(`` is the current character."""
    reader.advance()  # consume '('
    items: list = []
    while True:
        _skip_whitespace(reader)
        char = reader.peek()
        if char == ")":
            reader.advance()
            return items
        if char == _EOF:
            raise SExprError("unterminated list: missing closing ')'", reader.line, reader.col)
        items.append(_read_form(reader))


def _read_form(reader: _Reader) -> list | str | int | float:
    """Parse one value starting at the current character."""
    char = reader.peek()
    if char == "(":
        return _read_list(reader)
    if char == '"':
        return _read_string(reader)
    if char == _EOF:
        raise SExprError("unexpected end of input", reader.line, reader.col)
    return _read_atom(reader)


def _read_string(reader: _Reader) -> str:
    """Parse a double-quoted string and return its unescaped content."""
    start_line, start_col = reader.line, reader.col
    reader.advance()  # consume opening quote
    chars: list[str] = []
    while True:
        char = reader.advance()
        if char == _EOF:
            raise SExprError("unterminated string literal", start_line, start_col)
        if char == '"':
            return "".join(chars)
        if char == "\\":
            escaped = reader.advance()
            if escaped == _EOF:
                raise SExprError("unterminated string literal", start_line, start_col)
            chars.append(_unescape(escaped))
        else:
            chars.append(char)


def _unescape(char: str) -> str:
    """Map an escaped character to its value (lenient, like KiCad's reader)."""
    return {
        '"': '"',
        "\\": "\\",
        "n": "\n",
        "t": "\t",
        "r": "\r",
    }.get(char, char)


def _read_atom(reader: _Reader) -> str | int | float:
    """Parse a bare token (symbol or number) up to the next delimiter."""
    chars: list[str] = []
    while reader.peek() not in _ATOM_STOPPERS and reader.peek() != _EOF:
        chars.append(reader.advance())
    if reader.peek() == '"':
        raise SExprError("unexpected '\"' directly after an atom", reader.line, reader.col)
    if not chars:
        raise SExprError(f"unexpected character {reader.peek()!r}", reader.line, reader.col)
    return _coerce("".join(chars))


def _coerce(token: str) -> str | int | float:
    """Convert a numeric-looking token to int/float; keep symbols as strings."""
    if _INT_RE.fullmatch(token):
        return int(token)
    if _FLOAT_RE.fullmatch(token):
        return float(token)
    return token
