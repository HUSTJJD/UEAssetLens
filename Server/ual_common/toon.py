"""TOON v1 codec — UAL's only TOON implementation.

TOON = Token-Oriented Object Notation. A section declares its field schema once;
rows carry values only. See Server/schema/PROTOCOL.md for the authoritative spec.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

HEADER = "# UEAssetLens TOON v1 (Zero-VLM)"
_SECTION_RE = re.compile(r"^(\w+)\[(\d+)\]\{([^}]*)\}:\s*$")
_SUMMARY_RE = re.compile(r"^\s+([A-Za-z_][\w]*):\s*(.*)$")


@dataclass
class Table:
    name: str
    fields: Sequence[str]
    rows: list[Sequence[Any]] = field(default_factory=list)


def _scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def _escape(value: Any) -> str:
    text = _scalar(value)
    if any(ch in text for ch in (",", '"', "\n", "\r")) or text != text.strip():
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        escaped = escaped.replace("\n", "\\n").replace("\r", "\\r")
        return f'"{escaped}"'
    return text


def _unescape(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append({"n": "\n", "r": "\r", '"': '"', "\\": "\\"}.get(nxt, nxt))
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _split_row(line: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    in_quotes = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and in_quotes and i + 1 < len(line):
            buf.append(ch)
            buf.append(line[i + 1])
            i += 2
            continue
        if ch == '"':
            in_quotes = not in_quotes
            i += 1
            continue
        if ch == "," and not in_quotes:
            parts.append(_unescape("".join(buf)))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append(_unescape("".join(buf)))
    return parts


def render_toon(tables: Sequence[Table], summary: dict[str, Any] | None = None) -> str:
    """Render TOON text. Float precision is fixed to 4 decimals for stable diffs."""
    lines = [HEADER]
    if summary:
        lines.append("query_summary:")
        for key, value in summary.items():
            lines.append(f"  {key}: {_escape(value)}")
        lines.append("")
    for table in tables:
        lines.append(f"{table.name}[{len(table.rows)}]{{{','.join(table.fields)}}}:")
        for row in table.rows:
            values = list(row) + [""] * (len(table.fields) - len(row))
            lines.append("  " + ",".join(_escape(v) for v in values[: len(table.fields)]))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_toon(text: str) -> dict[str, Any]:
    """Parse TOON text back into {'summary': ..., 'tables': {name: [row dicts]}}.

    Used by tests and by CI to assert section/field order without regex guessing.
    """
    lines = text.splitlines()
    summary: dict[str, str] = {}
    tables: dict[str, list[dict[str, str]]] = {}
    header = ""
    current: str | None = None
    fields: list[str] = []

    for raw in lines:
        if raw.startswith("#"):
            header = raw
            continue
        if not raw.strip():
            continue
        if raw.strip() == "query_summary:":
            current = "query_summary"
            continue
        match = _SECTION_RE.match(raw.strip())
        if match:
            current = match.group(1)
            fields = [f.strip() for f in match.group(3).split(",")]
            tables[current] = []
            continue
        if raw.startswith("  ") or raw.startswith("\t"):
            body = raw.strip()
            if current == "query_summary":
                kv = _SUMMARY_RE.match(raw)
                if kv:
                    summary[kv.group(1)] = _split_row(kv.group(2))[0]
                continue
            if current in tables:
                values = _split_row(body)
                values += [""] * (len(fields) - len(values))
                tables[current].append(dict(zip(fields, values[: len(fields)])))
    return {"header": header, "summary": summary, "tables": tables}
