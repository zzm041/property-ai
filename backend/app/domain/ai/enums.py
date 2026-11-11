"""Deterministic normalization of model-emitted enum fields (PA-202).

The analysis contract (``app.domain.ai.models``) spells its enums in one
way only::

    category      water_supply electrical hvac cleaning security other
    severity      low medium high
    priority      p1 p2 p3 p4
    risk          none safety flooding electric gas other
    required_role STAFF
    required_skill maintenance cleaning security other

A 4B model does not always honour that spelling. It answers ``PLUMBING``,
``Water-Supply``, ``HIGH``, ``P0``, ``plumbing`` for the skill, and pydantic
then rejects the whole payload before any of it can be mapped, which loses
the analysis even though the content was usable.

This module folds the *spelling* only, deterministically, before the contract
model validates. Two invariants keep it fail-closed:

* the alias tables live here and nowhere else -- validators never carry their
  own alias logic;
* a value the tables do not know stays unknown. ``EnumNormalizationError``
  surfaces as ``AI.INVALID_OUTPUT`` / ``enum_not_mapped``; nothing is guessed
  from surrounding text, and no field is ever dropped.

Note on priority: the contract is ``p1..p4`` with **p1 the most urgent** (the
prompt says "priority选p1/p2/p3/p4之一", and ``_PRIORITY_ALIASES`` has shipped
p1->URGENT since PA-001). ``p0`` is accepted as a spelling of the top grade
because some models number from zero; ``p1..p4`` keep their shipped meaning, so
this table can never quietly downgrade an emergency by one grade.
"""
from collections.abc import Mapping
from dataclasses import dataclass

CATEGORY_ALIASES: dict[str, str] = {
    # water supply / plumbing
    "water_supply": "water_supply",
    "watersupply": "water_supply",
    "water": "water_supply",
    "supply_water": "water_supply",
    "plumbing": "water_supply",
    "plumber": "water_supply",
    "plumb": "water_supply",
    "pipe": "water_supply",
    "pipes": "water_supply",
    "drainage": "water_supply",
    # electrical
    "electrical": "electrical",
    "electric": "electrical",
    "electricity": "electrical",
    "electrics": "electrical",
    "power": "electrical",
    "lighting": "electrical",
    # hvac
    "hvac": "hvac",
    "hvac_system": "hvac",
    "air_conditioning": "hvac",
    "airconditioner": "hvac",
    "aircon": "hvac",
    "ac": "hvac",
    "heating_ventilation_air_conditioning": "hvac",
    # cleaning
    "cleaning": "cleaning",
    "clean": "cleaning",
    "cleaner": "cleaning",
    "housekeeping": "cleaning",
    "sanitation": "cleaning",
    # security
    "security": "security",
    "sec": "security",
    "guard": "security",
    "access_control": "security",
    # other
    "other": "other",
    "others": "other",
    "misc": "other",
    "miscellaneous": "other",
    "unknown": "other",
    "unclassified": "other",
}

SEVERITY_ALIASES: dict[str, str] = {
    "low": "low",
    "minor": "low",
    "slight": "low",
    "trivial": "low",
    "medium": "medium",
    "mid": "medium",
    "moderate": "medium",
    "normal": "medium",
    "high": "high",
    "severe": "high",
    "major": "high",
    "critical": "high",
}

PRIORITY_ALIASES: dict[str, str] = {
    # p1 is the most urgent grade of the shipped contract; p0 is the same grade
    # spelled from zero, never a grade above it.
    "p0": "p1",
    "priority0": "p1",
    "p1": "p1",
    "p2": "p2",
    "p3": "p3",
    "p4": "p4",
    "priority1": "p1",
    "priority2": "p2",
    "priority3": "p3",
    "priority4": "p4",
    "urgent": "p1",
    "highest": "p1",
    "critical": "p1",
    "emergency": "p1",
    "immediate": "p1",
    "high": "p2",
    "normal": "p3",
    "medium": "p3",
    "moderate": "p3",
    "low": "p4",
    "lowest": "p4",
    "minor": "p4",
}

RISK_ALIASES: dict[str, str] = {
    "none": "none",
    "no_risk": "none",
    "nil": "none",
    "safety": "safety",
    "personal_safety": "safety",
    "injury": "safety",
    "flooding": "flooding",
    "flood": "flooding",
    "water_damage": "flooding",
    "electric": "electric",
    "electrical": "electric",
    "electric_hazard": "electric",
    "shock": "electric",
    "gas": "gas",
    "gas_leak": "gas",
    "other": "other",
}

ROLE_ALIASES: dict[str, str] = {
    "staff": "STAFF",
    "staff_member": "STAFF",
    "employee": "STAFF",
    "technician": "STAFF",
    "worker": "STAFF",
}

REQUIRED_SKILL_ALIASES: dict[str, str] = {
    # Contract vocabulary only: case and separator folding. Trade labels
    # (electrician, plumber, hvac, guard) are deliberately NOT folded here.
    # PA-167 keeps the raw label so dispatch compares against the real trade
    # code; folding "electrician" to "maintenance" would erase that restriction
    # and let any maintenance group take an electrical job. A trade label on the
    # strict observation path therefore stays rejected instead of silently
    # downgraded.
    "maintenance": "maintenance",
    "cleaning": "cleaning",
    "security": "security",
    "other": "other",
}

ANALYSIS_ENUM_ALIASES: dict[str, dict[str, str]] = {
    "category": CATEGORY_ALIASES,
    "severity": SEVERITY_ALIASES,
    "priority": PRIORITY_ALIASES,
    "risk": RISK_ALIASES,
    "required_role": ROLE_ALIASES,
    "required_skill": REQUIRED_SKILL_ALIASES,
}

#: Fields the tables cover, in payload order; exported so a caller can report
#: ``schema_checked`` without re-deriving the list.
ANALYSIS_ENUM_FIELDS: tuple[str, ...] = tuple(ANALYSIS_ENUM_ALIASES)

#: ``required_skill`` is the one covered field whose value is deliberately
#: open: PA-167 carries an unknown label through as a *hard restriction* and
#: announces it (``SKILL_VALUE_NOT_NORMALIZED``). Folding only normalizes known
#: spellings and lets everything else pass untouched, so the contract keeps the
#: strict rejection for category/severity/priority/risk/role and the labelling
#: policy for skill stays where PA-167 put it.
FIELD_UNMAPPED_POLICY: dict[str, str] = {"required_skill": "pass_through"}

_SEPARATORS = str.maketrans({"-": "_", " ": "_", "/": "_", ".": "_", "\u3000": "_"})


def enum_key(raw: str) -> str:
    """Fold one raw value into the lookup key used by every table."""
    folded = raw.strip().casefold().translate(_SEPARATORS)
    while "__" in folded:
        folded = folded.replace("__", "_")
    return folded.strip("_")


@dataclass(frozen=True, slots=True)
class EnumNormalization:
    """One field the model spelled differently from the contract."""

    field: str
    raw: str
    normalized: str


class EnumNormalizationError(ValueError):
    """A covered enum field carried a value no table entry claims."""

    def __init__(self, field: str, raw: str) -> None:
        super().__init__(f"{field} is not a supported value: {raw!r}")
        self.field = field
        self.raw = raw


def normalize_enum(field: str, raw: str) -> str | None:
    """Return the contract value for ``raw``, or ``None`` when unmapped."""
    table = ANALYSIS_ENUM_ALIASES.get(field)
    if table is None:
        raise KeyError(f"{field} is not a normalized enum field")
    key = enum_key(raw)
    return table.get(key) or table.get(key.replace("_", ""))


def normalize_analysis_payload(
    payload: Mapping[str, object],
) -> tuple[dict[str, object], tuple[EnumNormalization, ...]]:
    """Return ``(payload, applied)`` with covered enum fields folded.

    Fields that are absent, empty or not strings are left untouched for the
    contract model to judge. A covered field with an unknown value raises
    :class:`EnumNormalizationError` -- fail-closed, never guessed.
    """
    normalized: dict[str, object] = dict(payload)
    applied: list[EnumNormalization] = []
    for field in ANALYSIS_ENUM_FIELDS:
        if field not in normalized:
            continue
        value = normalized[field]
        if not isinstance(value, str) or not value.strip():
            continue
        canonical = normalize_enum(field, value)
        if canonical is None:
            if FIELD_UNMAPPED_POLICY.get(field) == "pass_through":
                continue
            raise EnumNormalizationError(field, value)
        if canonical != value:
            normalized[field] = canonical
            applied.append(EnumNormalization(field=field, raw=value, normalized=canonical))
    return normalized, tuple(applied)


__all__ = [
    "ANALYSIS_ENUM_ALIASES",
    "ANALYSIS_ENUM_FIELDS",
    "FIELD_UNMAPPED_POLICY",
    "EnumNormalization",
    "EnumNormalizationError",
    "enum_key",
    "normalize_analysis_payload",
    "normalize_enum",
]
