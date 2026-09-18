"""Scale validation: synthetic industrial board generator + versioned fixture.

The versioned fixture ``industrial-74.kicad_net`` is the ground truth used to
validate the 500+ component scale path *before* the auditor is pointed at real
industrial boards. The generator is deterministic: the fixture must regenerate
byte for byte, and the auditor's findings over the fixture must equal the
generator's own ground-truth manifest — the seeded faults (with exact
evidence), and nothing else.

``board.expected`` is computed from the *board model*, not from the auditor, so
these tests are an independent cross-check: a divergence between the two
implementations fails loudly here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcbai.design.audit import audit_design
from pcbai.design.generator import ALL_FAULT_RULES, generate_board, generate_netlist
from pcbai.kicad.netlist import parse_netlist
from pcbai.kicad.sexpr import SExprError, parse

FIXTURES = Path(__file__).parent / "fixtures"
INDUSTRIAL = FIXTURES / "industrial-74.kicad_net"

N_COMPONENTS = 75
SEED = 0

#: Topology of the versioned fixture (both asserted and regenerated).
FIXTURE_NETS = 58
FIXTURE_FINDINGS = 21
FIXTURE_ERRORS = 2
FIXTURE_WARNINGS = 9
FIXTURE_INFOS = 10


def _audit_findings(design) -> set[tuple[str, tuple[str, ...]]]:
    report = audit_design(design)
    return {(finding.rule, tuple(finding.evidence)) for finding in report.findings}


def _seeded_findings(board) -> set[tuple[str, tuple[str, ...]]]:
    return {(fault.rule, fault.evidence) for fault in board.seeded}


def _manifest(board) -> set[tuple[str, tuple[str, ...]]]:
    return {(finding.rule, finding.evidence) for finding in board.expected}


class TestIndustrialFixture:
    """The versioned fixture parses at scale and is exactly the generator output."""

    def test_fixture_parses_75_components(self) -> None:
        design = parse_netlist(INDUSTRIAL)
        assert len(design.components) == N_COMPONENTS
        assert len(design.nets) == FIXTURE_NETS

    def test_fixture_regenerates_byte_for_byte(self) -> None:
        # generate_netlist(75, seed=0) must be the committed fixture, byte
        # for byte — the fixture is never a hand-tuned blob.
        assert generate_netlist(N_COMPONENTS, seed=SEED) == INDUSTRIAL.read_text(encoding="utf-8")

    def test_generated_board_matches_manifest_counts(self) -> None:
        board = generate_board(N_COMPONENTS, seed=SEED)
        assert board.component_count == N_COMPONENTS
        assert board.net_count == FIXTURE_NETS


class TestSeededFaults:
    """Every seeded fault is found with its exact rule and evidence."""

    def test_all_seeded_fault_components_detected(self) -> None:
        board = generate_board(N_COMPONENTS, seed=SEED)
        findings = _audit_findings(parse_netlist(INDUSTRIAL))
        seeded = _seeded_findings(board)
        # Every seeded fault appears in the audit report with identical
        # evidence (rule + ref + net names), the real detection contract.
        assert seeded <= findings

    def test_every_fault_rule_is_seeded(self) -> None:
        board = generate_board(N_COMPONENTS, seed=SEED)
        assert {fault.rule for fault in board.seeded} == set(ALL_FAULT_RULES)

    def test_no_false_positives_on_healthy_nets(self) -> None:
        # Every warning and error over the whole board must be one of the
        # seeded faults: nothing on a healthy net may be flagged. (Some
        # seeded faults — CAP_DERATING — are info-level by design, so the
        # comparison is a subset, not equality.)
        board = generate_board(N_COMPONENTS, seed=SEED)
        report = audit_design(parse_netlist(INDUSTRIAL))
        flagged = {
            (finding.rule, tuple(finding.evidence))
            for finding in report.findings
            if finding.severity in ("error", "warning")
        }
        assert flagged <= _seeded_findings(board)

    def test_audit_matches_full_manifest(self) -> None:
        # The complete ground truth: seeded faults PLUS the topology-driven
        # NO_DRIVER advisories the board model predicts. Set equality on
        # (rule, evidence) pairs is the strongest possible contract.
        board = generate_board(N_COMPONENTS, seed=SEED)
        assert _audit_findings(parse_netlist(INDUSTRIAL)) == _manifest(board)

    def test_manifest_severity_mix(self) -> None:
        board = generate_board(N_COMPONENTS, seed=SEED)
        assert len(board.seeded) == 13  # 14 fault components, 13 findings
        assert len(board.fault_refs) == 14
        assert len(board.expected) == FIXTURE_FINDINGS
        report = audit_design(parse_netlist(INDUSTRIAL)).summary
        assert (report["errors"], report["warnings"], report["infos"]) == (
            FIXTURE_ERRORS,
            FIXTURE_WARNINGS,
            FIXTURE_INFOS,
        )

    def test_healthy_majority(self) -> None:
        # The board is mostly healthy: fewer than a quarter of the parts are
        # fault components, so a report shows a realistic n/4 fault density.
        board = generate_board(N_COMPONENTS, seed=SEED)
        healthy = board.component_count - len(board.fault_refs)
        assert healthy >= 0.75 * board.component_count


class TestGeneratorDeterminism:
    """Same seed -> same board; different seed -> different values, same shape."""

    def test_same_seed_is_stable(self) -> None:
        assert generate_netlist(N_COMPONENTS, seed=SEED) == generate_netlist(
            N_COMPONENTS, seed=SEED
        )

    def test_different_seed_changes_values_not_topology(self) -> None:
        other = generate_board(N_COMPONENTS, seed=42)
        first = generate_board(N_COMPONENTS, seed=SEED)
        assert generate_netlist(N_COMPONENTS, seed=42) != generate_netlist(N_COMPONENTS, seed=SEED)
        assert other.component_count == first.component_count == N_COMPONENTS
        assert other.net_count == first.net_count == FIXTURE_NETS

    def test_fault_selection_subset(self, tmp_path: Path) -> None:
        board = generate_board(N_COMPONENTS, seed=SEED, faults=["E_SERIES_COMPLIANCE"])
        design = parse_netlist(_render(board, tmp_path / "subset.kicad_net"))
        assert board.component_count == N_COMPONENTS
        assert {finding.rule for finding in audit_design(design).findings} == {
            "E_SERIES_COMPLIANCE",
            "NO_DRIVER",
        }
        assert audit_design(design).summary["errors"] == 0
        assert _audit_findings(design) == _manifest(board)

    def test_unknown_fault_rule_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown fault rule"):
            generate_board(N_COMPONENTS, faults=["NOT_A_RULE"])

    def test_minimum_component_validation(self) -> None:
        # 9 core components + all 14 fault components = 23 minimum.
        with pytest.raises(ValueError, match="no room"):
            generate_board(n_components=10)


class TestIndustrialScale:
    """The 500-component scale path keeps every guarantee."""

    def _board_500(self):
        return generate_board(500, seed=SEED)

    def test_scale_500_generates_exactly_500(self) -> None:
        assert self._board_500().component_count == 500

    def test_scale_500_parses_audits_exactly(self, tmp_path: Path) -> None:
        board = self._board_500()
        design = parse_netlist(_render(board, tmp_path / "industrial-500.kicad_net"))
        assert len(design.components) == 500
        assert _audit_findings(design) == _manifest(board)
        # Fault density stays under a quarter of the board.
        report = audit_design(design).summary
        assert report["errors"] + report["warnings"] <= 500 / 4

    def test_scale_500_deterministic(self) -> None:
        assert generate_netlist(500, seed=SEED) == generate_netlist(500, seed=SEED)

    def test_strict_single_top_level_format(self) -> None:
        # The format contract: exactly ONE top-level (export ...) form and
        # no trailing content — the parser rejects anything else.
        text = INDUSTRIAL.read_text(encoding="utf-8")
        assert parse(text)  # parses cleanly
        for bad in (text.strip() + ")", text + '\n(export (version "X"))'):
            with pytest.raises(SExprError):
                parse(bad)


def _render(board, path: Path) -> Path:
    path.write_text(board.render(), encoding="utf-8")
    return path
