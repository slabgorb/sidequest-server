"""CreatureCore — shared fields for Character and NPC.

Port of sidequest_game::creature_core (creature_core.rs, 129 LOC).
Story 1-13: Extracted from Character and NPC via composition.

EdgePool is the composure currency (Epic 39). Replaces the old hp/max_hp/ac
fields. Stories 39-1 through 39-6 tune thresholds, recovery triggers, and
per-class base_max values.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from sidequest.game.status import Status, migrate_legacy_statuses

# Default placeholder base_max for EdgePool when per-class YAML isn't wired (story 39-3).
PLACEHOLDER_EDGE_BASE_MAX: int = 10


class RecoveryTrigger(str):
    """Recovery trigger values for EdgePool.

    Port of sidequest_genre::RecoveryTrigger (re-exported via creature_core).
    Using str constants rather than Enum to avoid import coupling.
    """

    OnResolution = "OnResolution"
    OnRest = "OnRest"
    OnSceneChange = "OnSceneChange"


class EdgeThreshold(BaseModel):
    """A downward threshold on an EdgePool.

    Port of sidequest_game::creature_core::EdgeThreshold.
    P1-required: thresholds appear in JSON; must round-trip.
    """

    model_config = {"extra": "forbid"}

    at: int
    event_id: str
    narrator_hint: str


class EdgePool(BaseModel):
    """First-class composure pool (Epic 39, story 39-1).

    Port of sidequest_game::creature_core::EdgePool.
    Replaces legacy hp/max_hp fields. current is clamped to [0, max].

    P1-required: narrator uses edge/max_edge for health-state framing.
    P2-deferred: recovery_triggers / thresholds (advancement/combat systems).
    """

    model_config = {"extra": "forbid"}

    current: int
    max: int
    base_max: int
    # P2-deferred: recovery trigger wiring (story 39-4/5/6 — combat/advancement)
    recovery_triggers: list[str] = Field(default_factory=list)
    # P2-deferred: threshold event emission (story 39-6 — advancement effects)
    thresholds: list[EdgeThreshold] = Field(default_factory=list)

    def apply_delta(self, delta: int) -> int:
        """Apply a composure delta. Returns new current value.

        Positive delta increases current (capped at max).
        Negative delta decreases current (floored at 0).
        """
        raw = self.current + delta
        self.current = max(0, min(self.max, raw))
        return self.current


def placeholder_edge_pool() -> EdgePool:
    """Build the default EdgePool used in constructors without YAML tuning."""
    return EdgePool(
        current=PLACEHOLDER_EDGE_BASE_MAX,
        max=PLACEHOLDER_EDGE_BASE_MAX,
        base_max=PLACEHOLDER_EDGE_BASE_MAX,
        recovery_triggers=[RecoveryTrigger.OnResolution],
        thresholds=[],
    )


class EdgeConfigMissingClassError(KeyError):
    """Genre pack declared `edge_config` but omitted a `base_max_by_class`
    entry for the character's class.

    Port of sidequest_game::creature_core::EdgeConfigMissingClassError.
    Raised loudly by edge_pool_from_config (Story 39-3) — silently
    reverting to the placeholder pool would hide content bugs. SOUL.md:
    fail loud at the boundary.

    Subclasses KeyError because this is conceptually a dict lookup miss;
    callers can still catch it as KeyError.
    """

    def __init__(self, class_name: str) -> None:
        self.class_name = class_name
        super().__init__(
            f"edge_config.base_max_by_class missing entry for class '{class_name}'"
        )


def edge_pool_from_config(edge_config: object, class_name: str) -> EdgePool:
    """Build a genre-authored EdgePool from an EdgeConfig and a class name.

    Port of sidequest_game::creature_core::edge_pool_from_config.

    Resolves base_max from edge_config.base_max_by_class[class] (raises
    EdgeConfigMissingClassError when absent), converts every
    EdgeThresholdDecl to an EdgeThreshold, and seeds recovery_triggers
    with OnResolution. The crossing-direction tag from YAML is
    informational — all EdgePool thresholds fire on downward crossings
    by construction.

    The `edge_config` parameter is typed as `object` to avoid a circular
    import with sidequest.genre.models.rules (which imports from the
    genre layer). Duck-typing suffices: we rely on `base_max_by_class`
    and `thresholds` attributes, both typed in the genre EdgeConfig
    model.
    """
    base_max_by_class = getattr(edge_config, "base_max_by_class", {})
    if class_name not in base_max_by_class:
        raise EdgeConfigMissingClassError(class_name=class_name)
    base_max = base_max_by_class[class_name]

    thresholds: list[EdgeThreshold] = []
    for decl in getattr(edge_config, "thresholds", []):
        thresholds.append(
            EdgeThreshold(
                at=decl.at,
                event_id=decl.event_id,
                narrator_hint=decl.narrator_hint,
            )
        )

    return EdgePool(
        current=base_max,
        max=base_max,
        base_max=base_max,
        recovery_triggers=[RecoveryTrigger.OnResolution],
        thresholds=thresholds,
    )


class Inventory(BaseModel):
    """Character inventory ledger — append-only item history and gold.

    Port of sidequest_game::inventory::Inventory (subset for Phase 1).
    Full item evolution (narrative_weight thresholds) is P2-deferred.
    """

    model_config = {"extra": "forbid"}

    items: list[dict] = Field(default_factory=list)
    gold: int = 0


class CreatureCore(BaseModel):
    """Shared fields for any creature (Character or NPC).

    Port of sidequest_game::creature_core::CreatureCore.
    Embedded via composition in both Character and Npc.
    In Rust, #[serde(flatten)] exposes all fields at the parent level.

    P1-required: name, description, personality, level, edge, inventory, statuses.
    P2-deferred: acquired_advancements (advancement system, Epic 39-8).
    """

    model_config = {"extra": "forbid"}

    name: str
    description: str
    personality: str
    level: int = 1
    xp: int = 0
    inventory: Inventory = Field(default_factory=Inventory)
    statuses: list[Status] = Field(default_factory=list)
    edge: EdgePool = Field(default_factory=placeholder_edge_pool)
    # P2-deferred: advancement tracking (epic 39-8, mechanical progression)
    acquired_advancements: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_statuses(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        raw = data.get("statuses")
        if raw is None:
            return data
        if isinstance(raw, list):
            data = {**data, "statuses": migrate_legacy_statuses(raw)}
        return data

    @field_validator("name")
    @classmethod
    def name_non_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name cannot be blank")
        return v

    @field_validator("description")
    @classmethod
    def description_non_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("description cannot be blank")
        return v

    @field_validator("personality")
    @classmethod
    def personality_non_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("personality cannot be blank")
        return v

    def apply_edge_delta(self, delta: int) -> int:
        """Apply an edge delta and return the new current value."""
        return self.edge.apply_delta(delta)
