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

import re
from dataclasses import dataclass, field
from pathlib import Path

# A data-row token: a double-quoted string (which may contain spaces) or a
# run of non-whitespace.
_TOKEN_RE = re.compile(r'"[^"]*"|\S+')
_NUMBER_RE = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")


def _format_value(value: object, quoted: bool) -> str:
    text = str(value)
    # Quote string-typed fields (as the source file did), and anything that
    # would otherwise split the row or vanish: empty values, whitespace.
    if quoted or text == "" or any(c.isspace() for c in text):
        return '"' + text.replace('"', "'") + '"'
    return text


@dataclass
class CgatsTable:
    file_type: str  # e.g. "CTI1", "CTI2", "CTI3"
    preamble: list[str]  # header keyword lines before BEGIN_DATA_FORMAT, verbatim
    fields: list[str]
    rows: list[dict[str, str]]
    # Fields whose values were quoted in the source file (e.g. SAMPLE_LOC in
    # a .ti2), so a rewrite keeps them quoted.
    quoted_fields: set[str] = field(default_factory=set)

    def render(self) -> str:
        lines = [self.file_type, *self.preamble]
        lines.append(f"NUMBER_OF_FIELDS {len(self.fields)}")
        lines.append("BEGIN_DATA_FORMAT")
        lines.append(" ".join(self.fields))
        lines.append("END_DATA_FORMAT")
        lines.append(f"NUMBER_OF_SETS {len(self.rows)}")
        lines.append("BEGIN_DATA")
        for row in self.rows:
            lines.append(
                " ".join(_format_value(row.get(f, ""), f in self.quoted_fields) for f in self.fields)
            )
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
        quoted_fields: set[str] = set()
        while i < len(lines) and lines[i].strip() != "END_DATA":
            line = lines[i].strip()
            if line:
                # CGATS quotes string-typed fields (e.g. SAMPLE_LOC "A17");
                # confirmed against real printtarg .ti2 output. Strip
                # matching quotes so callers get the plain value.
                tokens = []
                for name, tok in zip(fields, _TOKEN_RE.findall(line)):
                    if len(tok) >= 2 and tok[0] == tok[-1] == '"':
                        tok = tok[1:-1]
                        if not _NUMBER_RE.match(tok):
                            quoted_fields.add(name)
                    tokens.append(tok)
                rows.append(dict(zip(fields, tokens)))
            i += 1
        i += 1  # skip END_DATA

        tables.append(
            CgatsTable(
                file_type=file_type,
                preamble=preamble,
                fields=fields,
                rows=rows,
                quoted_fields=quoted_fields,
            )
        )

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
