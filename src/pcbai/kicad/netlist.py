"""KiCad design models and parsers (netlist + documented schematic subset).

The exported KiCad netlist (``.net``) is the source of truth for
connectivity; the schematic parser covers a documented subset of
``.kicad_sch`` and is intentionally tolerant of everything else it finds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pcbai.kicad.sexpr import parse

__all__ = [
    "Component",
    "Design",
    "Label",
    "Net",
    "NetlistError",
    "Point",
    "SchematicDesign",
    "SchematicError",
    "Wire",
    "parse_netlist",
    "parse_schematic",
]

#: Netlist export format versions handled by :func:`parse_netlist`. ``"E"``
#: is the S-expression version written by Eeschema 6.x through 8.x.
SUPPORTED_NETLIST_VERSIONS: frozenset[str] = frozenset({"E"})

#: Schematic file versions handled by :func:`parse_schematic`.
#: ``20230121`` -> Eeschema 7.x, ``20231120`` -> Eeschema 8.x.
SUPPORTED_SCHEMATIC_VERSIONS: frozenset[int] = frozenset({20230121, 20231120})


class NetlistError(ValueError):
    """Raised when a KiCad netlist is structurally invalid or unsupported."""


class SchematicError(ValueError):
    """Raised when a KiCad schematic violates the supported subset."""


@dataclass(frozen=True)
class Component:
    """A component instance from a KiCad design.

    Attributes:
        ref: Reference designator (e.g. ``"R1"``, ``"J1"``).
        value: Value as written in the design (e.g. ``"330"``, ``"LED"``);
            ``None`` when absent. Empty strings are normalised to ``None``.
        footprint: KiCad footprint id (e.g. ``"Resistor_SMD:R_0603_1608Metric"``);
            ``None`` when absent.
        pins: Mapping pin number -> net name. The netlist parser fills every
            terminal that appears in a net; terminals in no net are absent.
            The schematic parser maps every pin number to ``None`` because
            pin-level connectivity cannot be derived from ``.kicad_sch``
            geometry (pin geometry lives in the symbol libraries).
    """

    ref: str
    value: str | None
    footprint: str | None
    pins: dict[str, str | None] = field(default_factory=dict)


@dataclass(frozen=True)
class Net:
    """An electrical net (label) and its pin connections.

    Attributes:
        name: Net name as written in the design.
        connections: ``(ref, pin_number)`` pairs for every pin attached to
            this net. Always empty for nets derived from schematic labels
            (see :func:`parse_schematic`).
    """

    name: str
    connections: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Design:
    """The components and nets of a KiCad design.

    Attributes:
        components: Reference -> component.
        nets: Net name -> net.
    """

    components: dict[str, Component] = field(default_factory=dict)
    nets: dict[str, Net] = field(default_factory=dict)


#: An absolute (x, y) point in millimeters.
Point = tuple[float, float]


@dataclass(frozen=True)
class Wire:
    """A straight schematic wire segment (absolute coordinates)."""

    start: Point
    end: Point


@dataclass(frozen=True)
class Label:
    """A named label (local, global or hierarchical) at a point."""

    name: str
    position: Point


@dataclass
class SchematicDesign(Design):
    """A :class:`Design` plus the raw geometry kept by :func:`parse_schematic`.

    In addition to the ``components``/``nets`` fields it carries the wires,
    labels and junctions that define net names and their geometrical
    connectivity (used to detect label-level shorts).
    """

    wires: list[Wire] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)
    junctions: list[Point] = field(default_factory=list)


def parse_netlist(source: str | Path) -> Design:
    """Parse a KiCad netlist (``.net``) into a :class:`Design`.

    Supported format: the S-expression export written by Eeschema 6.x-8.x::

        (export (version "E")
          (design (source ...) ...)
          (components
            (comp (ref "R1") (value "330") (footprint "...") ...))
          (nets
            (net (code 0) (name "GND")
              (node (ref "R1") (pin "1")) ...)))

    The top-level ``(version ...)`` tag is validated: only version ``"E"``
    is accepted; a missing or different version raises
    :class:`NetlistError`. Pin-level connectivity is taken from the nets
    section (the netlist is the source of truth).

    Unannotated reference placeholders (``R?``, ``U?`` — Eeschema marks
    components placed but never annotated with a trailing ``?``) are
    **omitted** from the design: several parts share the same placeholder
    ref, so ``(ref, pin)`` pairs are not unique and the identity is not
    stable enough to anchor evidence. Their net nodes are filtered too;
    a net left with zero real connections is kept (empty) so its name
    still exists, but no rule can produce a finding for it.

    ``source`` is a file path (``str``/``Path``) or raw S-expression text;
    see :func:`_read_source` for the exact rule. Raises
    :class:`NetlistError` on structural problems and
    :class:`pcbai.kicad.sexpr.SExprError` on syntax errors.
    """
    tree = parse(_read_source(source))
    return _build_from_netlist(tree)


def parse_schematic(source: str | Path) -> Design:
    """Parse a ``.kicad_sch`` schematic into a :class:`SchematicDesign`.

    Documented subset (Eeschema 7.x/8.x, versions 20230121 and 20231120):

    - ``(symbol ...)`` instances: ``(property "Reference" ...)``,
      ``(property "Value" ...)`` and ``(property "Footprint" ...)`` give the
      component data; ``(pin "N" (uuid ...))`` entries give pin *numbers*.
      Pin geometry lives in the symbol libraries and is not present in the
      schematic file, so pins carry no net here (the exported netlist is
      the source of truth for pin connectivity). Multi-unit parts merge
      their pin sets into a single component.
    - ``(wire (pts (xy x1 y1) (xy x2 y2)) ...)`` segments.
    - ``(label "NAME" (at x y) ...)`` plus ``(global_label ...)`` and
      ``(hierarchical_label ...)``: each defines a net name at a point.
    - ``(junction (at x y) ...)`` connection points.

    Nets are derived with a union-find over wire endpoints, junction points
    and label positions: labels joined by wires belong to the same net. A
    cluster that joins two different label names is a short circuit and
    raises :class:`SchematicError`. Nets derived this way carry an empty
    ``connections`` list.

    Everything else found in real files (sheets, embedded ``lib_symbols``,
    text, images, buses, net classes...) is skipped without crashing: the
    parser is tolerant, not strict.
    """
    tree = parse(_read_source(source))
    return _build_from_schematic(tree)


def _read_source(source: str | Path) -> str:
    """Read ``source`` if it names a file, otherwise treat it as raw text.

    ``str`` inputs are considered file paths when a file with that name
    exists, or when they look path-like (contain a separator or a known
    KiCad extension) and the file is missing — the latter raises
    ``FileNotFoundError`` so path typos fail loudly instead of parsing the
    path string as S-expression text.
    """
    if isinstance(source, Path):
        return source.read_text(encoding="utf-8")
    if source.lstrip().startswith("("):
        return source
    path = Path(source)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    if "/" in source or path.suffix in {".net", ".kicad_sch", ".kicad_pcb"}:
        raise FileNotFoundError(f"no such KiCad file: {source!r}")
    return source


def _children_named(node: list, name: str) -> list[list]:
    """Direct children of ``node`` whose head is ``name``."""
    return [child for child in node[1:] if isinstance(child, list) and child and child[0] == name]


def _first_child_named(node: list, name: str) -> list | None:
    """First direct child of ``node`` whose head is ``name`` (or ``None``)."""
    for child in node[1:]:
        if isinstance(child, list) and child and child[0] == name:
            return child
    return None


def _atom_text(atom: object) -> str:
    """Render a parsed atom as its KiCad text value."""
    if isinstance(atom, str):
        return atom
    if isinstance(atom, int | float):
        return str(atom)
    raise ValueError(f"expected a string or number atom, got {atom!r}")


def _optional_text(node: list | None) -> str | None:
    """Text value of ``node`` (second position), or ``None`` when absent.

    An empty string is normalised to ``None``: KiCad writes ``(value "")``
    for components without a value.
    """
    if node is None or len(node) < 2:
        return None
    text = _atom_text(node[1])
    return text or None


def _is_unannotated_ref(ref: str) -> bool:
    """True for KiCad's unannotated reference placeholder (``R?``).

    Eeschema writes a trailing ``?`` in the reference of components that
    were placed but never annotated (``R?``, ``U?``). Multiple instances
    share the same placeholder ref, so ``(ref, pin)`` pairs are not
    unique: the parts carry no stable identity and are omitted from the
    :class:`Design` (see :func:`parse_netlist`).
    """
    return ref.endswith("?")


def _build_from_netlist(tree: list) -> Design:
    """Assemble a :class:`Design` from a parsed ``(export ...)`` tree."""
    if not tree or tree[0] != "export":
        raise NetlistError("expected a top-level (export ...) form")
    version_node = _first_child_named(tree, "version")
    if version_node is None or len(version_node) < 2:
        raise NetlistError("netlist has no (version ...) tag")
    version = _atom_text(version_node[1])
    if version not in SUPPORTED_NETLIST_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_NETLIST_VERSIONS))
        raise NetlistError(f"unsupported netlist version {version!r} (supported: {supported})")

    components: dict[str, Component] = {}
    comps_node = _first_child_named(tree, "components")
    if comps_node is not None:
        for comp_node in _children_named(comps_node, "comp"):
            _add_component(components, comp_node)

    nets: dict[str, Net] = {}
    nets_node = _first_child_named(tree, "nets")
    if nets_node is not None:
        for net_node in _children_named(nets_node, "net"):
            _add_net(nets, net_node)

    _attach_pins(components, nets)
    return Design(components=components, nets=nets)


def _add_component(components: dict[str, Component], node: list) -> None:
    """Parse one ``(comp ...)`` entry into ``components``."""
    ref_node = _first_child_named(node, "ref")
    if ref_node is None or len(ref_node) < 2:
        raise NetlistError(f"component without a reference: {node!r}")
    ref = _atom_text(ref_node[1])
    if _is_unannotated_ref(ref):
        return  # placeholder R?: no stable identity (see parse_netlist)
    if ref in components:
        raise NetlistError(f"duplicate component reference {ref!r}")
    value = _optional_text(_first_child_named(node, "value"))
    footprint = _optional_text(_first_child_named(node, "footprint"))
    components[ref] = Component(ref=ref, value=value, footprint=footprint)


def _add_net(nets: dict[str, Net], node: list) -> None:
    """Parse one ``(net ...)`` entry into ``nets``."""
    name_node = _first_child_named(node, "name")
    if name_node is None or len(name_node) < 2:
        raise NetlistError(f"net without a name: {node!r}")
    name = _atom_text(name_node[1])
    if name in nets:
        raise NetlistError(f"duplicate net name {name!r}")
    connections: list[tuple[str, str]] = []
    for node_item in _children_named(node, "node"):
        ref_node = _first_child_named(node_item, "ref")
        pin_node = _first_child_named(node_item, "pin")
        if ref_node is None or len(ref_node) < 2 or pin_node is None or len(pin_node) < 2:
            raise NetlistError(f"malformed (node ...) entry: {node_item!r}")
        ref = _atom_text(ref_node[1])
        if _is_unannotated_ref(ref):
            continue  # placeholder R?: its pins carry no stable identity
        connection = (ref, _atom_text(pin_node[1]))
        if connection not in connections:
            connections.append(connection)
    nets[name] = Net(name=name, connections=connections)


def _attach_pins(components: dict[str, Component], nets: dict[str, Net]) -> None:
    """Fill ``component.pins`` from net connections (netlist is the truth)."""
    for net in nets.values():
        for ref, pin in net.connections:
            component = components.get(ref)
            if component is None:
                raise NetlistError(f"net {net.name!r} references unknown component {ref!r}")
            current = component.pins.get(pin)
            if current is not None and current != net.name:
                raise NetlistError(
                    f"component {ref!r} pin {pin!r} belongs to both {current!r} and {net.name!r}"
                )
            component.pins[pin] = net.name


def _build_from_schematic(tree: list) -> SchematicDesign:
    """Assemble a :class:`SchematicDesign` from a parsed ``(kicad_sch ...)`` tree."""
    if not tree or tree[0] != "kicad_sch":
        raise SchematicError("expected a top-level (kicad_sch ...) form")
    version_node = _first_child_named(tree, "version")
    if version_node is None or len(version_node) < 2:
        raise SchematicError("schematic has no (version ...) tag")
    version = version_node[1]
    if version not in SUPPORTED_SCHEMATIC_VERSIONS:
        supported = ", ".join(str(v) for v in sorted(SUPPORTED_SCHEMATIC_VERSIONS))
        raise SchematicError(f"unsupported schematic version {version!r} (supported: {supported})")

    components = _parse_schematic_components(tree)
    wires = _parse_wires(tree)
    labels = _parse_labels(tree)
    junctions = _parse_junctions(tree)
    nets = _derive_label_nets(wires=wires, labels=labels, junctions=junctions)
    return SchematicDesign(
        components=components,
        nets=nets,
        wires=wires,
        labels=labels,
        junctions=junctions,
    )


def _parse_schematic_components(root: list) -> dict[str, Component]:
    """Symbols at the schematic root -> components, merging multi-unit parts."""
    components: dict[str, Component] = {}
    for symbol in _children_named(root, "symbol"):
        reference = _property_value(symbol, "Reference")
        if reference is None:
            raise SchematicError(f"symbol without a Reference property: {symbol!r}")
        value = _property_value(symbol, "Value")
        footprint = _property_value(symbol, "Footprint")
        pin_numbers: list[str] = []
        for pin in _children_named(symbol, "pin"):
            if len(pin) < 2 or not isinstance(pin[1], str | int | float):
                continue
            pin_numbers.append(str(pin[1]))
        existing = components.get(reference)
        if existing is None:
            components[reference] = Component(
                ref=reference,
                value=value,
                footprint=footprint,
                pins=dict.fromkeys(pin_numbers),
            )
        else:
            existing.pins.update(dict.fromkeys(pin_numbers))
    return components


def _property_value(symbol: list, name: str) -> str | None:
    """Text of a ``(property "<name>" "<value>" ...)`` child, if present."""
    for prop in _children_named(symbol, "property"):
        if len(prop) < 3:
            continue
        if prop[1] == name:
            text = _atom_text(prop[2])
            return text or None
    return None


def _parse_wires(root: list) -> list[Wire]:
    """Collect ``(wire (pts (xy x1 y1) (xy x2 y2)) ...)`` segments."""
    wires: list[Wire] = []
    for wire in _children_named(root, "wire"):
        pts = _first_child_named(wire, "pts")
        if pts is None:
            continue
        points: list[Point] = []
        for xy in pts[1:]:
            if isinstance(xy, list):
                point = _point_xy(xy)
                if point is not None:
                    points.append(point)
        for index in range(len(points) - 1):
            wires.append(Wire(start=points[index], end=points[index + 1]))
    return wires


def _parse_labels(root: list) -> list[Label]:
    """Collect local, global and hierarchical labels with their positions."""
    labels: list[Label] = []
    for kind in ("label", "global_label", "hierarchical_label"):
        for node in _children_named(root, kind):
            if len(node) < 2 or not isinstance(node[1], str):
                continue
            position = _at_position(_first_child_named(node, "at"))
            if position is not None:
                labels.append(Label(name=node[1], position=position))
    return labels


def _parse_junctions(root: list) -> list[Point]:
    """Collect ``(junction (at x y) ...)`` connection points."""
    junctions: list[Point] = []
    for junction in _children_named(root, "junction"):
        position = _at_position(_first_child_named(junction, "at"))
        if position is not None:
            junctions.append(position)
    return junctions


def _point_xy(node: list) -> Point | None:
    """Absolute coordinates of a ``(xy x y)`` node, if parseable."""
    if len(node) < 3 or node[0] != "xy":
        return None
    try:
        return (float(node[1]), float(node[2]))
    except (TypeError, ValueError):
        return None


def _at_position(node: list | None) -> Point | None:
    """The (x, y) point of an ``(at x y [angle])`` node, if parseable."""
    if node is None or len(node) < 3:
        return None
    try:
        return (float(node[1]), float(node[2]))
    except (TypeError, ValueError):
        return None


class _UnionFind:
    """Tiny union-find over hashable keys (wire/label connectivity)."""

    __slots__ = ("_parent",)

    def __init__(self) -> None:
        self._parent: dict[Point, Point] = {}

    def find(self, key: Point) -> Point:
        parent = self._parent.setdefault(key, key)
        while parent != self._parent[parent]:
            self._parent[parent] = self._parent[self._parent[parent]]
            parent = self._parent[parent]
        return parent

    def union(self, left: Point, right: Point) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self._parent[right_root] = left_root


def _derive_label_nets(
    wires: list[Wire], labels: list[Label], junctions: list[Point]
) -> dict[str, Net]:
    """Net names from label/wire connectivity (union-find over points).

    A connected cluster holding two different label names is a short
    circuit and raises :class:`SchematicError`.
    """
    union_find = _UnionFind()
    for wire in wires:
        union_find.union(wire.start, wire.end)
    for position in junctions:
        union_find.find(position)
    for label in labels:
        union_find.find(label.position)

    clusters: dict[Point, set[str]] = {}
    for label in labels:
        root = union_find.find(label.position)
        clusters.setdefault(root, set()).add(label.name)

    nets: dict[str, Net] = {}
    for names in clusters.values():
        if len(names) > 1:
            joined = ", ".join(repr(name) for name in sorted(names))
            raise SchematicError(f"short circuit: labels {joined} are connected by wires")
        name = next(iter(names))
        nets[name] = Net(name=name, connections=[])
    return nets
