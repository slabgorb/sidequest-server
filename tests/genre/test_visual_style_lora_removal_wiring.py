"""Wiring tests for Story 43-1: VisualStyle LoRA-field removal.

These tests exercise the *system*, not the model in isolation:

- Production source (sidequest/) must contain no `.lora`, `.lora_trigger`,
  `.lora_scale`, or `.lora_path` attribute access — guards against the
  field being silently re-added or accessed via duck typing.
- The genre-pack loader must successfully load a real, clean pack
  (heavy_metal) and surface a VisualStyle with the expected core fields.
- The genre-pack loader must tolerate a real pack whose
  `visual_style.yaml` still contains a `loras:` block (e.g.
  `elemental_harmony`). Story 43-4 owns scrubbing those YAMLs; 43-1
  must not regress loader compatibility while they remain.
"""

from __future__ import annotations

import re
from pathlib import Path

from sidequest.genre.loader import load_genre_pack
from sidequest.genre.models import VisualStyle

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_SRC = REPO_ROOT / "sidequest-server" / "sidequest"
CONTENT_GENRE_PACKS = REPO_ROOT / "sidequest-content" / "genre_packs"


_LORA_ATTR_PATTERN = re.compile(
    r"\.(lora|lora_trigger|lora_scale|lora_path)\b"
)


def _iter_server_python_files() -> list[Path]:
    """Yield every .py file under SERVER_SRC, skipping __pycache__ trees."""
    return [
        p for p in SERVER_SRC.rglob("*.py")
        if "__pycache__" not in p.parts
    ]


class TestNoLoraAttributeAccessInServer:
    def test_no_lora_attribute_access_in_production_code(self) -> None:
        """No production module may read .lora / .lora_trigger /
        .lora_scale / .lora_path off any object. Story 43-2 will scrub the
        daemon side; this test covers the server side and is the wiring
        proof for AC3.

        Scope note: the regex matches dot-prefixed attribute access only
        (e.g. `vs.lora`). It will not flag bare-word mentions of `lora_*`
        in comments, docstrings, or string literals — those would require
        an AST walk. If a future story wants comment/docstring coverage,
        widen the pattern; for now AC3's "grep lora_ across server" is
        satisfied by the attribute-access form, which is what production
        code actually does.
        """
        offenders: list[str] = []
        for path in _iter_server_python_files():
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if _LORA_ATTR_PATTERN.search(line):
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}"
                    )
        assert offenders == [], (
            "Found .lora* attribute access in production code:\n"
            + "\n".join(offenders)
        )


class TestVisualStyleLoaderStillWorks:
    def test_load_clean_pack_yields_visual_style(self) -> None:
        """heavy_metal/visual_style.yaml has no LoRA references; it must
        round-trip through the loader to a VisualStyle whose values match
        the on-disk pack content.
        """
        pack = load_genre_pack(CONTENT_GENRE_PACKS / "heavy_metal")
        visual_style = pack.visual_style
        assert isinstance(visual_style, VisualStyle), (
            f"Expected VisualStyle, got {type(visual_style).__name__}"
        )
        # heavy_metal's positive_suffix opens with the Doré reference;
        # the substring is stable on-disk content and a sharper assertion
        # than truthy.
        assert "Doré" in visual_style.positive_suffix, (
            "heavy_metal positive_suffix must reference Gustave Doré "
            f"(got: {visual_style.positive_suffix!r})"
        )
        assert visual_style.preferred_model == "dev", (
            f"heavy_metal preferred_model expected 'dev', "
            f"got {visual_style.preferred_model!r}"
        )

    def test_load_pack_with_legacy_lora_block(self) -> None:
        """`elemental_harmony/visual_style.yaml` still ships a top-level
        `loras:` block (Story 43-4 will scrub it). The loader must tolerate
        that block today — pack must load, visual_style must be a
        VisualStyle, and the unknown `loras` key must round-trip into
        `__pydantic_extra__` (proving `extra='allow'` survived).

        This is the load-bearing wiring proof for AC3/AC4: the deletion of
        the typed LoRA fields does not break a real, on-disk pack that
        still references them.
        """
        pack = load_genre_pack(CONTENT_GENRE_PACKS / "elemental_harmony")
        visual_style = pack.visual_style
        assert isinstance(visual_style, VisualStyle), (
            f"Expected VisualStyle, got {type(visual_style).__name__}"
        )
        # The pack's `loras:` block lives at the top level of
        # visual_style.yaml — it must survive into __pydantic_extra__
        # because extra='allow' is preserved on the model. If a future
        # change flips extra to 'ignore', the legacy block would be
        # silently dropped and this assertion would catch it.
        extras = visual_style.__pydantic_extra__ or {}
        assert "loras" in extras, (
            "elemental_harmony's `loras:` block was dropped during load — "
            "extra='allow' may have regressed. Got extras keys: "
            f"{sorted(extras.keys())}"
        )
        loras = extras["loras"]
        assert isinstance(loras, list) and len(loras) >= 1, (
            f"elemental_harmony loras must be a non-empty list, got {loras!r}"
        )
        # Typed-field surface: even when `loras:` is present in the YAML,
        # nothing LoRA-shaped lands on the typed model.
        for forbidden in ("lora", "lora_trigger", "lora_scale", "lora_path"):
            assert forbidden not in type(visual_style).model_fields, (
                f"VisualStyle.{forbidden} re-introduced into model_fields"
            )


class TestVisualStyleSchemaSurface:
    def test_visual_style_declared_fields_match_post_removal_set(self) -> None:
        """Lock down the post-removal field surface so a future edit can't
        silently re-add a LoRA field. The set is the four core image-gen
        fields plus the visual_tag_overrides map — anything else (or
        anything missing) breaks this test deliberately.
        """
        expected = {
            "positive_suffix",
            "negative_prompt",
            "preferred_model",
            "base_seed",
            "visual_tag_overrides",
        }
        declared = set(VisualStyle.model_fields.keys())
        assert declared == expected, (
            f"VisualStyle declared fields drifted: "
            f"unexpected={declared - expected}, missing={expected - declared}"
        )
