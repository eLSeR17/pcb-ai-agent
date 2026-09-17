"""Tests for the dependency-free S-expression parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from pcbai.kicad.sexpr import SExprError, parse, parse_file


class TestParse:
    def test_nested_lists_and_mixed_atoms(self) -> None:
        assert parse('(a (b "c") 1)') == ["a", ["b", "c"], 1]

    def test_empty_lists(self) -> None:
        assert parse("()") == []
        assert parse("(a () (b))") == ["a", [], ["b"]]

    def test_strings_with_escapes(self) -> None:
        text = '(x "a\\"b" "c\\\\d" "")'
        assert parse(text) == ["x", 'a"b', "c\\d", ""]

    def test_numbers_int_and_float(self) -> None:
        assert parse("(1 -2 3.5 -0.25 1e3 2.5e-2)") == [1, -2, 3.5, -0.25, 1000.0, 0.025]

    def test_symbols_keep_special_characters(self) -> None:
        text = "(Device:R Resistor_SMD:R_0603_1608Metric /5V #PWR01 nil 5V @tag x*y 1.2.3)"
        assert parse(text) == [
            "Device:R",
            "Resistor_SMD:R_0603_1608Metric",
            "/5V",
            "#PWR01",
            "nil",
            "5V",
            "@tag",
            "x*y",
            "1.2.3",
        ]

    def test_whitespace_and_newlines_are_ignored_between_forms(self) -> None:
        text = '(\n  (design\n    (source "x")\n  )\n)\n'
        assert parse(text) == [["design", ["source", "x"]]]

    def test_parse_file(self, tmp_path: Path) -> None:
        path = tmp_path / "sample.sexpr"
        path.write_text('(export (version "E"))', encoding="utf-8")
        assert parse_file(path) == ["export", ["version", "E"]]


class TestParseErrors:
    def test_empty_input(self) -> None:
        with pytest.raises(SExprError) as exc_info:
            parse("")
        assert exc_info.value.line == 1
        assert exc_info.value.column == 1
        assert "expected an S-expression" in str(exc_info.value)

    def test_top_level_atom_rejected(self) -> None:
        with pytest.raises(SExprError) as exc_info:
            parse("42")
        assert exc_info.value.column == 1
        assert "expected '('" in str(exc_info.value)

    def test_unmatched_closing_parenthesis_at_top_level(self) -> None:
        with pytest.raises(SExprError) as exc_info:
            parse(")")
        assert exc_info.value.column == 1
        assert "unmatched ')'" in str(exc_info.value)

    def test_unterminated_string_reports_start_position(self) -> None:
        with pytest.raises(SExprError) as exc_info:
            parse('(\n  (a "oops\n')
        assert exc_info.value.message == "unterminated string literal"
        assert exc_info.value.line == 2
        assert exc_info.value.column == 6

    def test_unbalanced_parenthesis_reports_end_position(self) -> None:
        with pytest.raises(SExprError) as exc_info:
            parse("(a (b)")
        assert exc_info.value.line == 1
        assert exc_info.value.column == 7
        assert "unterminated list" in str(exc_info.value)

    def test_trailing_content_after_top_level_form(self) -> None:
        with pytest.raises(SExprError) as exc_info:
            parse("(a) (b)")
        assert exc_info.value.column == 5
        assert "trailing content" in str(exc_info.value)
