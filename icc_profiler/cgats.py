"""Minimal reader/writer for the CGATS-ish files ArgyllCMS uses (.ti1/.ti2/.ti3).

Confirmed against real ArgyllCMS 3.1.0 output (see plan verification step 2):
a single .ti1/.ti2/.ti3 file can contain MULTIPLE concatenated CGATS tables
(targen's .ti1 output has three: the actual patch list targen/printtarg use,
plus two diagnostic "extreme values" tables). Each table restarts with its
own file-type line (e.g. "CTI1") and has its own preamble/DATA_FORMAT/DATA
section. This module parses and preserves ALL tables, since naively only
reading/writing the first would silently corrupt the others (confirmed: a
naive single-table rewrite broke printtarg with
"Read 8 sets, expected N sets"). Code that only cares about one table (the
common case for .ti2/.ti3, which are single-table) should still go through
`read_tables`/`write_tables` so any other table present is never dropped.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class CgatsTable:
    file_type: str  # e.g. "CTI1", "CTI2", "CTI3"
    preamble: list[str]  # header keyword lines before BEGIN_DATA_FORMAT, verbatim
    fields: list[str]
    rows: list[dict[str, str]]

    def render(self) -> str:
        lines = [self.file_type, *self.preamble]
        lines.append(f"NUMBER_OF_FIELDS {len(self.fields)}")
        lines.append("BEGIN_DATA_FORMAT")
        lines.append(" ".join(self.fields))
        lines.append("END_DATA_FORMAT")
        lines.append(f"NUMBER_OF_SETS {len(self.rows)}")
        lines.append("BEGIN_DATA")
        for row in self.rows:
            lines.append(" ".join(str(row.get(f, "")) for f in self.fields))
        lines.append("END_DATA")
        return "\n".join(lines)

    def write(self, path: Path) -> None:
        """Write this table alone. Only safe for files known to be single-table
        (.ti2/.ti3 as produced by this project); use write_tables() to update
        one table of a multi-table file (e.g. .ti1) without losing the rest."""
        path.write_text(self.render() + "\n")


def read_tables(path: Path) -> list[CgatsTable]:
    text = path.read_text()
    lines = [line.rstrip("\n") for line in text.splitlines()]
    if not lines:
        raise ValueError(f"{path} is empty")

    tables: list[CgatsTable] = []
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        file_type = lines[i].strip()
        i += 1
        preamble: list[str] = []
        while i < len(lines) and lines[i].strip() != "BEGIN_DATA_FORMAT":
            if lines[i].strip() and not lines[i].strip().startswith("NUMBER_OF_FIELDS"):
                preamble.append(lines[i])
            i += 1
        if i >= len(lines):
            raise ValueError(f"{path}: no BEGIN_DATA_FORMAT section found after {file_type}")
        i += 1  # skip BEGIN_DATA_FORMAT
        fields = lines[i].split() if i < len(lines) else []
        i += 1
        while i < len(lines) and lines[i].strip() != "END_DATA_FORMAT":
            i += 1
        i += 1  # skip END_DATA_FORMAT

        while i < len(lines) and lines[i].strip() != "BEGIN_DATA":
            i += 1
        if i >= len(lines):
            raise ValueError(f"{path}: no BEGIN_DATA section found for {file_type} table")
        i += 1  # skip BEGIN_DATA
        rows: list[dict[str, str]] = []
        while i < len(lines) and lines[i].strip() != "END_DATA":
            line = lines[i].strip()
            if line:
                # CGATS quotes string-typed fields (e.g. SAMPLE_LOC "A17");
                # confirmed against real printtarg .ti2 output. Strip
                # matching quotes so callers get the plain value.
                tokens = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] == '"' else t for t in line.split()]
                rows.append(dict(zip(fields, tokens)))
            i += 1
        i += 1  # skip END_DATA

        tables.append(CgatsTable(file_type=file_type, preamble=preamble, fields=fields, rows=rows))

    if not tables:
        raise ValueError(f"{path}: no CGATS tables found")
    return tables


def write_tables(path: Path, tables: list[CgatsTable]) -> None:
    path.write_text("\n".join(t.render() for t in tables) + "\n")


def read(path: Path) -> CgatsTable:
    """Convenience for the common single-table-of-interest case: returns the
    FIRST table (the one targen/printtarg/scanin/colprof actually act on for
    .ti1/.ti2/.ti3). Any other tables present are not dropped by this call
    alone -- only a subsequent write() of just this table would do that; use
    read_tables()/write_tables() when modifying a file that may have more
    than one table (currently: .ti1)."""
    return read_tables(path)[0]
