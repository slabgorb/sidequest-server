"""Genre-pack projection.yaml rule schema.

Rules are pydantic models. Three rule types in v1:
    - TargetOnlyRule  — include only for recipients named by a payload field.
    - IncludeIfRule   — whole-event include gated by a predicate.
    - RedactFieldsRule — mask fields unless predicate holds for the viewer.

A single YAML rule entry must carry exactly one of target_only/
include_if/redact_fields (enforced by model_validator). Kinds can appear
in multiple rule entries; they compose (Task 14).

Semantic validation (kind exists, predicate exists, field paths
resolve against payload schema, type-compatible masks) is Task 10.
"""

from __future__ import annotations

import re
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

_PRED_RE = re.compile(r"^([a-z_][a-z0-9_]*)\((.*)\)$")


class PredicateCall(BaseModel):
    """A parsed predicate invocation, e.g. visible_to(target)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    predicate: str
    arg: str | None

    @classmethod
    def parse(cls, expr: str) -> PredicateCall:
        m = _PRED_RE.match(expr.strip())
        if not m:
            raise ValueError(f"invalid predicate expression: {expr!r}")
        name, arg = m.group(1), m.group(2).strip()
        return cls(predicate=name, arg=arg if arg else None)


class TargetOnlySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    field: str


class RedactSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    field: str
    unless: PredicateCall
    mask: object

    @model_validator(mode="before")
    @classmethod
    def _parse_unless(cls, data: object) -> object:
        if isinstance(data, dict) and isinstance(data.get("unless"), str):
            data = {**data, "unless": PredicateCall.parse(data["unless"])}
        return data


class _RuleBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str


class TargetOnlyRule(_RuleBase):
    target_only: TargetOnlySpec


class IncludeIfRule(_RuleBase):
    include_if: PredicateCall

    @model_validator(mode="before")
    @classmethod
    def _parse_include_if(cls, data: object) -> object:
        if isinstance(data, dict) and isinstance(data.get("include_if"), str):
            data = {**data, "include_if": PredicateCall.parse(data["include_if"])}
        return data


class RedactFieldsRule(_RuleBase):
    redact_fields: list[RedactSpec] = Field(default_factory=list)


class VisibilityTagSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # No fields yet — the rule reads _visibility from payload. Reserved for
    # future GM-panel overrides.


class VisibilityTagRule(_RuleBase):
    visibility_tag: VisibilityTagSpec


ProjectionRule = Annotated[
    TargetOnlyRule | IncludeIfRule | RedactFieldsRule | VisibilityTagRule,
    Field(discriminator=None),
]


class ProjectionRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules: list[ProjectionRule]

    @model_validator(mode="before")
    @classmethod
    def _disambiguate_rule_variants(cls, data: object) -> object:
        """A single rule entry must carry exactly one of the three action keys."""
        if not isinstance(data, dict):
            return data
        raw_rules = data.get("rules")
        if not isinstance(raw_rules, list):
            return data

        coerced: list[dict] = []
        for r in raw_rules:
            if not isinstance(r, dict):
                raise ValueError(f"rule entry must be a mapping, got {type(r).__name__}")
            present = [
                k
                for k in ("target_only", "include_if", "redact_fields", "visibility_tag")
                if k in r
            ]
            if len(present) != 1:
                raise ValueError(
                    f"rule for kind={r.get('kind')!r} must carry exactly one of "
                    f"target_only/include_if/redact_fields/visibility_tag; found {present}"
                )
            coerced.append(r)
        return {**data, "rules": coerced}


def load_rules_from_yaml_str(text: str) -> ProjectionRules:
    raw = yaml.safe_load(text) or {}
    if not isinstance(raw, dict):
        raise ValueError("projection.yaml root must be a mapping")
    return ProjectionRules.model_validate(raw)


def load_rules_from_yaml_path(path) -> ProjectionRules:
    with open(path) as f:
        return load_rules_from_yaml_str(f.read())
