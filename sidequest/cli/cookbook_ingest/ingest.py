"""Idempotent SRD → corpus transform (spec §4.1).

Source: vendored BTMorton/dnd-5e-srd JSON (Prereq 0) — a recursively
nested document, NOT a flat array. A stat-block leaf is any dict whose
`content[0]` (after markdown strip) matches the stat line and which
carries a `**Challenge**` line. The dict KEY is the monster name. No
silent fallback: an unparseable CR raises (CLAUDE.md). Idempotent.

This parser is PROVEN (planning run: 316 monster rows, Mummy edge
handled, required marquee/big_bad names resolved).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

_SIZES = "Tiny|Small|Medium|Large|Huge|Gargantuan"
_STATLINE = re.compile(rf"^({_SIZES})\s+([A-Za-z][A-Za-z ]*?)(?:\s*\(([^)]+)\))?,\s*(.+)$")
_CHALLENGE = re.compile(r"Challenge\s*([0-9/]+)\s*\(([\d,]+)\s*XP\)")
_ITEM_RARITY = re.compile(r"\b(very rare|rare|uncommon|common|legendary|artifact)\b", re.IGNORECASE)


def parse_cr(raw: str) -> float:
    """'1/8'->0.125, '1/4'->0.25, '21'->21.0. Raises on garbage."""
    text = str(raw).strip()
    try:
        if "/" in text:
            num, _, den = text.partition("/")
            return float(num) / float(den)
        return float(text)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"unparseable CR: {raw!r}") from exc


def _strip_md(line: str) -> str:
    """Remove inline bold/italic noise: '*Medium undead**,** LE*' → clean."""
    return line.replace("**", "").strip().strip("*").strip()


def parse_statline(raw: str) -> tuple[str, str, list[str], str]:
    """(size, Type, [tags], alignment) from the leaf's content[0]."""
    m = _STATLINE.match(_strip_md(raw))
    if not m:
        raise ValueError(f"unparseable stat line: {raw!r}")
    size, typ, tags, align = m.groups()
    tag_list = [t.strip().lower() for t in tags.split(",")] if tags else []
    return size, typ.strip().title(), tag_list, align.strip()


def _is_leaf(node: Any) -> bool:
    c = node.get("content") if isinstance(node, dict) else None
    return (
        isinstance(c, list)
        and bool(c)
        and isinstance(c[0], str)
        and _STATLINE.match(_strip_md(c[0])) is not None
    )


def iter_statblock_leaves(node: Any, name: str = "") -> Iterator[tuple[str, dict]]:
    """Yield (name, node) for every stat-block leaf, recursively."""
    if not isinstance(node, dict):
        return
    if _is_leaf(node):
        yield name, node
        return
    for key, child in node.items():
        if key == "content":
            continue
        yield from iter_statblock_leaves(child, key)


def walk_monsters(docs: list[Any]) -> list[dict[str, Any]]:
    """Flatten all docs to spec §4.1 rows. De-dup by name (first wins).

    Source order is preserved (doc order, then document order); the
    fidelity test asserts emitted ≤ source leaves and required names
    survive.
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for doc in docs:
        for name, leaf in iter_statblock_leaves(doc):
            if not name or name in seen:
                continue
            size, typ, tags, align = parse_statline(leaf["content"][0])
            cr = xp = None
            for line in leaf["content"]:
                if not isinstance(line, str):
                    continue
                h = _CHALLENGE.search(line.replace("**", ""))
                if h:
                    cr = parse_cr(h.group(1))
                    xp = int(h.group(2).replace(",", ""))
                    break
            if cr is None:
                continue  # not a combat stat block (no Challenge line)
            seen.add(name)
            rows.append(
                {
                    "name": name,
                    "size": size,
                    "type": typ,
                    "tags": tags,
                    "alignment": align,
                    "cr": cr,
                    "xp": xp,
                    "source": "SRD 5.1",
                }
            )
    return rows


def parse_item_desc(desc: str) -> tuple[str, str] | None:
    """'<Type>[ (detail)], <rarity>[ (notes)]' → (Type, Rarity).

    Real SRD 5.1 forms (verified against the vendored source): 'Wondrous
    item, rare (requires attunement)', 'Armor (medium or heavy, but not
    hide), uncommon', 'Weapon (any ammunition), uncommon (+1), rare
    (+2), or very rare (+3)', 'Rod, uncommon'. The item type is the head
    before the first '(' or ',' — the parenthetical is a subtype detail,
    NOT part of the category that loot_bias keys on. Rarity is the first
    rarity word ('very rare' before 'rare', longest-match). Returns None
    when no rarity word is present (not a graded magic item — skipped,
    consistent with the prior contract; no silent type-mangling).
    """
    m = _ITEM_RARITY.search(desc)
    if not m:
        return None
    head = desc[: m.start()]
    delims = [i for i in (head.find("("), head.find(",")) if i != -1]
    item_type = head[: min(delims) if delims else len(head)].strip()
    if not item_type:
        return None
    return item_type, m.group(1)


def walk_items(item_doc: Any) -> list[dict[str, Any]]:
    """Magic-items doc → spec §4.1 item rows. The italic descriptor line
    carries item_type + rarity + attunement; Task documents samples."""
    rows: list[dict[str, Any]] = []
    root = item_doc.get("Magic Items", item_doc)
    for name, node in root.items():
        if name == "content" or not isinstance(node, dict):
            continue
        content = node.get("content")
        if not (isinstance(content, list) and content and isinstance(content[0], str)):
            continue
        desc = _strip_md(content[0])
        parsed = parse_item_desc(desc)
        if parsed is None:
            continue
        item_type, rarity = parsed
        attune = "attunement" in " ".join(x for x in content if isinstance(x, str)).lower()
        rows.append(
            {
                "name": name,
                "item_type": item_type.title(),
                "rarity": rarity.title(),
                "attunement": attune,
                "notes": "",
                "source": "SRD 5.1",
            }
        )
    return rows


def _dump(rows: list[dict[str, Any]], dest: Path) -> None:
    dest.write_text(yaml.safe_dump(rows, sort_keys=False, allow_unicode=True))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cookbook_ingest")
    ap.add_argument("--world", required=True, type=Path)
    args = ap.parse_args(argv)
    src = args.world / "corpus/_source"
    out = args.world / "corpus"
    needed = ["monsters.json", "creatures.json", "npcs.json", "magic_items.json"]
    missing = [f for f in needed if not (src / f).exists()]
    if missing:
        print(
            f"FATAL: missing vendored SRD source {missing} under {src} (Prereq 0)", file=sys.stderr
        )
        return 2
    docs = [
        json.loads((src / f).read_text()) for f in ("monsters.json", "creatures.json", "npcs.json")
    ]
    _dump(walk_monsters(docs), out / "monsters.yaml")
    _dump(walk_items(json.loads((src / "magic_items.json").read_text())), out / "items.yaml")
    print(f"ingested → {out}/monsters.yaml, {out}/items.yaml")
    return 0
