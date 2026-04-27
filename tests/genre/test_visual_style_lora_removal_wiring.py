"""Wiring tests for Story 43-1: VisualStyle LoRA-field removal.

These tests fail in RED until the LoRA fields drop from VisualStyle and
no production code path references them. They exercise the *system*, not
the model in isolation:

- Production source (sidequest/) must contain no `.lora`, `.lora_trigger`,
  `.lora_scale`, or `.lora_path` attribute access — guards against the
  field being silently re-added or accessed via duck typing.
- The genre-pack loader must successfully load a real, clean pack
  (heavy_metal) and surface a VisualStyle with the expected core fields.
- The loader must tolerate legacy YAMLs that still mention `lora:` —
  Story 43-4 owns scrubbing the YAMLs, so 43-1 must not break them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from sidequest.genre.loader import load_genre_pack
from sidequest.genre.models import VisualStyle

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_SRC = REPO_ROOT / "sidequest-server" / "sidequest"
CONTENT_GENRE_PACKS = REPO_ROOT / "sidequest-content" / "genre_packs"


_LORA_ATTR_PATTERN = re.compile(
    r"\.(lora|lora_trigger|lora_scale|lora_path)\b"
)


def _iter_server_python_files() -> list[Path]:
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
        """
        offenders: list[str] = []
        for path in _iter_server_python_files():
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                # Skip comments and docstrings is hard without an AST;
                # keep it simple — string-literal mentions of "lora_" in
                # docstrings should be scrubbed too if they describe a
                # removed field. Story 43-1 scope explicitly says
                # "verify no callers remain (grep lora_ across server)".
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
        round-trip through the loader to a VisualStyle.
        """
        pack = load_genre_pack(CONTENT_GENRE_PACKS / "heavy_metal")
        # The loader exposes the visual style under the GenrePack model.
        # Different access paths exist across the codebase; this test
        # tolerates either `pack.visual_style` (typed) or accessing it via
        # a dict key on the parsed pack.
        visual_style = getattr(pack, "visual_style", None)
        assert visual_style is not None, (
            "load_genre_pack(heavy_metal) must surface a visual_style"
        )
        assert isinstance(visual_style, VisualStyle), (
            f"Expected VisualStyle, got {type(visual_style).__name__}"
        )
        assert visual_style.positive_suffix, (
            "VisualStyle.positive_suffix must be populated for heavy_metal"
        )
        assert visual_style.preferred_model, (
            "VisualStyle.preferred_model must be populated for heavy_metal"
        )

    def test_visual_style_tolerates_legacy_lora_yaml(
        self, tmp_path: Path
    ) -> None:
        """A YAML that still has top-level singular `lora:` / `lora_trigger:`
        / `lora_scale:` keys must continue to load (extra='allow'). 43-4
        owns the YAML cleanup; 43-1 must not regress legacy compatibility.
        """
        yaml_path = tmp_path / "visual_style.yaml"
        yaml_path.write_text(
            yaml.safe_dump(
                {
                    "positive_suffix": "old style",
                    "negative_prompt": "blur",
                    "preferred_model": "flux1",
                    "base_seed": 7,
                    "lora": "legacy.safetensors",
                    "lora_trigger": "legacy_trigger",
                    "lora_scale": 0.8,
                }
            ),
            encoding="utf-8",
        )
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        # Direct model_validate proves the schema (post-removal) still
        # parses legacy YAMLs without raising.
        vs = VisualStyle.model_validate(raw)
        assert vs.positive_suffix == "old style"
        # Typed-field assertions: the LoRA keys are NOT exposed as
        # declared fields, even when present in the raw YAML.
        assert "lora" not in type(vs).model_fields
        assert "lora_trigger" not in type(vs).model_fields
        assert "lora_scale" not in type(vs).model_fields


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


@pytest.mark.parametrize(
    "field_name",
    ["lora", "lora_trigger", "lora_scale", "lora_path"],
)
def test_visual_style_field_removed_parametrized(field_name: str) -> None:
    """Parametrized guard rail — explicit per-field assertion mirroring
    the AC1 enumeration so the failure message names the offending field.
    """
    assert field_name not in VisualStyle.model_fields, (
        f"VisualStyle.{field_name} must be removed per ADR-070 supersession"
    )
