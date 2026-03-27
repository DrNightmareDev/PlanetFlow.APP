from __future__ import annotations

from collections import Counter
from math import ceil
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app import sde
from app.database import get_db
from app.dependencies import require_account
from app.i18n import get_language_from_request, translate, translate_type_name
from app.market import PI_TYPE_IDS
from app.models import Character
from app.pi_data import (
    ALL_P1,
    ALL_P2,
    ALL_P3,
    ALL_P4,
    P0_TO_P1,
    P1_TO_P2,
    P2_TO_P3,
    P3_TO_P4,
    PLANET_RESOURCES,
    PLANET_TYPE_COLORS,
)
from app.routers.dashboard import _attach_pi_skills, _build_dashboard_payload, _load_colony_cache, _save_colony_cache
from app.routers.system import _load_system_planets
from app.templates_env import templates

router = APIRouter(prefix="/colony-plan", tags=["colony-plan"])

P1_TO_P0 = {v: k for k, v in P0_TO_P1.items()}
TIER_ORDER = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}
FACTORY_ANY_TYPE = "Factory"


def _resolve_type_id(name: str) -> int | None:
    return PI_TYPE_IDS.get(name) or sde.find_type_id_by_name(name)


def _product_tier(name: str) -> str | None:
    if name in ALL_P4:
        return "P4"
    if name in ALL_P3:
        return "P3"
    if name in ALL_P2:
        return "P2"
    if name in ALL_P1:
        return "P1"
    return None


def _build_product_labels(lang: str) -> dict[str, str]:
    names = set(ALL_P1) | set(ALL_P2) | set(ALL_P3) | set(ALL_P4) | set(P0_TO_P1.keys())
    labels: dict[str, str] = {}
    for name in names:
        labels[name] = translate_type_name(_resolve_type_id(name), fallback=name, lang=lang)
    return labels


def _build_planet_type_labels(lang: str) -> dict[str, str]:
    return {
        name: translate(f"planet_type.{name.lower()}", lang=lang, default=name)
        for name in PLANET_TYPE_COLORS
    }


def _parse_csv_ints(raw: str | None) -> list[int]:
    values: list[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if part.isdigit():
            values.append(int(part))
    return list(dict.fromkeys(values))


def _system_meta(system_id: int) -> dict[str, Any]:
    info = sde.get_system_local(system_id) or {}
    return {
        "id": system_id,
        "name": info.get("name", f"System {system_id}"),
        "security": info.get("security", 0.0),
        "region": info.get("region_name", ""),
        "constellation": info.get("constellation_name", ""),
    }


def _collect_chain(product_name: str) -> dict[str, Any]:
    tier = _product_tier(product_name)
    if not tier:
        raise ValueError(f"Unknown PI product: {product_name}")

    tiers = {"P1": set(), "P2": set(), "P3": set(), "P4": set()}
    flow_edges: list[tuple[str, str]] = []
    p0_needed: set[str] = set()

    def visit(name: str) -> None:
        current_tier = _product_tier(name)
        if not current_tier:
            return
        if name in tiers[current_tier]:
            return
        tiers[current_tier].add(name)
        if current_tier == "P1":
            p0_name = P1_TO_P0.get(name)
            if p0_name:
                p0_needed.add(p0_name)
            return
        inputs: list[str]
        if current_tier == "P2":
            inputs = P1_TO_P2.get(name, [])
        elif current_tier == "P3":
            inputs = P2_TO_P3.get(name, [])
        else:
            inputs = P3_TO_P4.get(name, [])
        for item in inputs:
            if _product_tier(item):
                flow_edges.append((item, name))
                visit(item)
            else:
                # Direct P1 input in certain P4 recipes
                flow_edges.append((item, name))
                visit(item)

    visit(product_name)

    # Detect self-sufficient P2 opportunities: a P2 whose two P1 inputs can both
    # be extracted on the same planet type.  One planet can then run two ECUs
    # (P0_A + P0_B) and the matching P2 factory, saving two planet slots.
    self_sufficient_p2: dict[str, dict[str, Any]] = {}
    for p2_name in sorted(tiers["P2"]):
        p1_inputs = P1_TO_P2.get(p2_name, [])
        if len(p1_inputs) != 2:
            continue
        p1_a, p1_b = p1_inputs
        p0_a = P1_TO_P0.get(p1_a)
        p0_b = P1_TO_P0.get(p1_b)
        if not p0_a or not p0_b or p0_a == p0_b:
            continue
        types_a = {pt for pt, rs in PLANET_RESOURCES.items() if p0_a in rs}
        types_b = {pt for pt, rs in PLANET_RESOURCES.items() if p0_b in rs}
        common = sorted(types_a & types_b)
        if common:
            self_sufficient_p2[p2_name] = {
                "p0_a": p0_a, "p0_b": p0_b,
                "p1_a": p1_a, "p1_b": p1_b,
                "planet_types": common,
            }

    return {
        "product": product_name,
        "tier": tier,
        "tiers": {k: sorted(v) for k, v in tiers.items()},
        "p0_needed": sorted(p0_needed),
        "flow_edges": flow_edges,
        "self_sufficient_p2": self_sufficient_p2,
    }


def _load_cached_colonies(account, characters: list[Character], db: Session) -> list[dict]:
    cached = _load_colony_cache(account.id, db)
    if not cached:
        payload = _build_dashboard_payload(account, characters, db, price_mode=getattr(account, "price_mode", "sell"))
        _save_colony_cache(account.id, payload, db)
        cached = {"colonies": payload["colonies"]}
    return list(cached.get("colonies", []))


def _character_capacity(char: Character, used_colonies: int) -> dict[str, Any]:
    skill_map = {skill["name"]: skill["level"] for skill in getattr(char, "pi_skills", [])}
    ip_level = int(skill_map.get("Interplanetary Consolidation", 0) or 0)
    has_scope = bool(getattr(char, "has_skill_scope", False))
    max_planets = 1 + ip_level if has_scope else max(used_colonies, 6)
    return {
        "id": char.id,
        "name": char.character_name,
        "portrait": char.portrait_url,
        "existing_total": used_colonies,
        "max_planets": max_planets,
        "remaining_slots": max(max_planets - used_colonies, 0),
        "scope_based": has_scope,
    }


def _pick_system_subset(
    selected_systems: list[dict],
    required_p0: set[str],
    selected_colonies: list[dict],
    single_system_only: bool,
) -> tuple[list[dict], dict[str, Any]]:
    if not single_system_only:
        return selected_systems, {"single_system_mode": False, "chosen_system": None, "viable": True}

    viable: list[tuple[int, dict]] = []
    colony_counter = Counter(int(col.get("solar_system_id") or 0) for col in selected_colonies)
    for system in selected_systems:
        resources = set()
        for planet in system["planets"]:
            resources.update(PLANET_RESOURCES.get(planet["planet_type"], []))
        if required_p0.issubset(resources):
            score = colony_counter.get(system["id"], 0) * 100 + len(system["planets"])
            viable.append((score, system))

    if not viable:
        return [], {
            "single_system_mode": True,
            "chosen_system": None,
            "viable": False,
            "missing_resources": sorted(required_p0),
        }

    viable.sort(key=lambda item: item[0], reverse=True)
    chosen = viable[0][1]
    return [chosen], {
        "single_system_mode": True,
        "chosen_system": chosen,
        "viable": True,
    }


def _make_planet_pool(
    selected_systems: list[dict],
    selected_char_ids: set[int],
    colonies: list[dict],
    char_id_by_name: dict[str, int],
) -> tuple[list[dict], dict[int, int], set[int]]:
    occupied_by_selected: dict[int, int] = {}
    blocked_planets: set[int] = set()
    for colony in colonies:
        planet_id = int(colony.get("planet_id") or 0)
        if not planet_id:
            continue
        char_id = char_id_by_name.get(colony.get("character_name") or "")
        if char_id in selected_char_ids:
            occupied_by_selected[planet_id] = char_id
        else:
            blocked_planets.add(planet_id)

    pool: list[dict] = []
    for system in selected_systems:
        for planet in system["planets"]:
            pid = int(planet["planet_id"])
            if pid in blocked_planets:
                continue
            resources = PLANET_RESOURCES.get(planet["planet_type"], [])
            pool.append({
                **planet,
                "resources": resources,
                "occupied_char_id": occupied_by_selected.get(pid),
            })
    return pool, occupied_by_selected, blocked_planets


def _feasibility_analysis(
    *,
    chain: dict[str, Any],
    scoped_systems: list[dict],
    char_state: dict[int, dict],
    planet_pool: list[dict],
    blocked_planets: set[int],
    all_colonies: list[dict],
    char_id_by_name: dict[str, int],
    selected_char_ids: set[int],
) -> dict[str, Any]:
    p0_count = len(chain["p0_needed"])
    factory_count = 0 if chain["tier"] == "P1" else sum(len(chain["tiers"][tier]) for tier in ("P2", "P3", "P4"))

    # Count achievable self-sufficient P2 assignments: each saves 2 planets
    # (1 combined planet replaces P0_A extractor + P0_B extractor + P2 factory).
    #
    # IMPORTANT: different characters can operate on the same physical planet
    # simultaneously (each has their own command center).  The feasibility
    # simulation therefore does NOT mark planets as "exclusively used" between
    # SS P2 assignments — it only checks that a planet of the required type
    # exists in the pool.  Character capacity is the real limiting factor and
    # is checked separately via total_capacity vs planets_needed.
    ss_covered_p0: set[str] = set()
    ss_planet_count = 0
    for p2_name, ss in chain.get("self_sufficient_p2", {}).items():
        p0_a, p0_b = ss["p0_a"], ss["p0_b"]
        planet_available = any(
            p0_a in p.get("resources", []) and p0_b in p.get("resources", [])
            for p in planet_pool
        )
        if planet_available:
            ss_covered_p0.add(p0_a)
            ss_covered_p0.add(p0_b)
            ss_planet_count += 1

    # planets_needed with self-sufficient optimisations applied:
    # each SS P2 uses 1 planet for 3 roles (P0_A + P0_B + P2), saving 2 slots.
    planets_needed = (p0_count - len(ss_covered_p0)) + (factory_count - ss_planet_count) + ss_planet_count

    # Include relocation slots: chars with 0 free slots can repurpose existing
    # colonies in selected systems. existing_reuse assignments commit a colony
    # in-place so they also reduce available relocation capacity.
    total_capacity = sum(
        max(state["remaining_slots"], 0)
        + max(
            state.get("relocation_slots", 0)
            - state.get("relocation_assignments", 0)
            - state.get("existing_reuse_assignments", 0),
            0,
        )
        for state in char_state.values()
    )
    capacity_ok = total_capacity >= planets_needed

    available_resources: set[str] = set()
    for planet in planet_pool:
        available_resources.update(planet.get("resources", []))

    all_system_resources: set[str] = set()
    blocked_planet_type: dict[int, str] = {}
    blocked_planet_name: dict[int, str] = {}
    blocked_planet_system: dict[int, str] = {}
    for system in scoped_systems:
        for planet in system["planets"]:
            pid = int(planet["planet_id"])
            planet_resources = PLANET_RESOURCES.get(planet.get("planet_type", ""), [])
            all_system_resources.update(planet_resources)
            if pid in blocked_planets:
                blocked_planet_type[pid] = planet.get("planet_type", "")
                blocked_planet_name[pid] = planet.get("planet_name", f"Planet {pid}")
                blocked_planet_system[pid] = system.get("name", "")

    blocked_by: dict[int, str] = {}
    for colony in all_colonies:
        pid = int(colony.get("planet_id") or 0)
        if not pid:
            continue
        char_name = colony.get("character_name") or ""
        cid = char_id_by_name.get(char_name)
        if cid not in selected_char_ids and pid in blocked_planets:
            blocked_by[pid] = char_name

    covered: list[str] = []
    blocked_p0: list[dict[str, Any]] = []
    uncoverable_p0: list[str] = []
    insufficient_p0: list[str] = []  # planet exists but consumed by a higher-priority P0

    # Greedy simulation mirroring the actual assignment order:
    #   1. self-sufficient P2 assignments (covers two P0s on one planet)
    #   2. remaining P0 extractors
    # Detects true planet-count conflicts for standalone P0 extractions (those not
    # already handled by a self-sufficient P2).  Planets shared by SS assignments
    # are NOT blocked here — different chars may still use the same planet.
    simulated_used: set[int] = set()
    insufficient_set: set[str] = set()
    for p0_name in chain["p0_needed"]:
        if p0_name in ss_covered_p0:
            continue  # already handled by a self-sufficient P2 assignment
        candidates = [
            p for p in planet_pool
            if p0_name in p.get("resources", [])
            and int(p["planet_id"]) not in simulated_used
        ]
        if candidates:
            simulated_used.add(int(candidates[0]["planet_id"]))
        elif p0_name in available_resources:
            # A planet exists for this resource but it was already claimed by an
            # earlier P0 in the assignment order.
            insufficient_set.add(p0_name)

    for p0_name in chain["p0_needed"]:
        if p0_name in insufficient_set:
            insufficient_p0.append(p0_name)
        elif p0_name in available_resources:
            covered.append(p0_name)
        elif p0_name in all_system_resources:
            hints = []
            for pid in blocked_planets:
                ptype = blocked_planet_type.get(pid)
                if ptype and p0_name in PLANET_RESOURCES.get(ptype, []):
                    hints.append({
                        "planet_id": pid,
                        "planet_name": blocked_planet_name.get(pid, f"Planet {pid}"),
                        "system_name": blocked_planet_system.get(pid, ""),
                        "blocked_by": blocked_by.get(pid, "unknown"),
                    })
            blocked_p0.append({"p0": p0_name, "hints": hints})
        else:
            uncoverable_p0.append(p0_name)

    # Check that P4 products have at least one Barren or Temperate planet available.
    # The Advanced Production Plant can only be built on those two types.
    p4_ok = True
    no_p4_planet = len(chain["tiers"].get("P4", [])) > 0 and not any(
        p.get("planet_type") in ("Barren", "Temperate") for p in planet_pool
    )
    if no_p4_planet:
        p4_ok = False

    return {
        "planets_needed": planets_needed,
        "total_capacity": total_capacity,
        "capacity_ok": capacity_ok,
        "p4_ok": p4_ok,
        "no_p4_planet": no_p4_planet,
        "p0_ok": not blocked_p0 and not uncoverable_p0 and not insufficient_p0,
        "feasible": capacity_ok and not blocked_p0 and not uncoverable_p0 and not insufficient_p0 and p4_ok,
        "covered_p0": covered,
        "blocked_p0": blocked_p0,
        "uncoverable_p0": uncoverable_p0,
        "insufficient_p0": insufficient_p0,
    }


def _select_assignment(
    *,
    candidates: list[dict],
    char_state: dict[int, dict],
    assigned_planet_chars: set[tuple[int, int]],
    include_unassigned: bool,
    preferred_chars: set[int] | None = None,
    preferred_systems: set[int] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool]:
    """Returns (planet, char_state, is_relocation)."""
    preferred_chars = preferred_chars or set()
    # (planet, state, existing_planet, is_relocation)
    eligible: list[tuple[dict[str, Any], dict[str, Any], bool, bool]] = []

    for planet in candidates:
        planet_id = int(planet["planet_id"])
        occupied_char_id = planet.get("occupied_char_id")
        # All chars are candidates — different chars may share the same planet.
        # The occupant gets a score bonus but any char can build a new command center.
        candidate_char_ids = list(char_state.keys())

        for char_id in candidate_char_ids:
            if not char_id:
                continue
            state = char_state.get(char_id)
            if not state:
                continue
            # Each (planet, char) pair can only be assigned once.
            # Different chars may share the same planet (each has their own command center).
            if (planet_id, char_id) in assigned_planet_chars:
                continue
            existing_planet = occupied_char_id == char_id
            if existing_planet:
                # Cap existing-reuse at max_planets too — the char can't hold
                # more colonies than their skill allows.
                total_plan = (
                    state["existing_reuse_assignments"]
                    + state["new_assignments"]
                    + state["relocation_assignments"]
                )
                if total_plan >= state["max_planets"]:
                    continue
                is_relocation = False
            else:
                can_new = state["remaining_slots"] > 0
                # existing_reuse assignments already commit a colony slot in-place,
                # so they reduce available relocation capacity too
                reloc_free = (
                    state["relocation_slots"]
                    - state["relocation_assignments"]
                    - state["existing_reuse_assignments"]
                )
                can_relocate = state["remaining_slots"] <= 0 and reloc_free > 0
                if not can_new and not can_relocate:
                    continue
                if not include_unassigned and state["selected_system_existing"] == 0:
                    continue
                is_relocation = not can_new
            eligible.append((planet, state, existing_planet, is_relocation))

    if not eligible:
        return None, None, False

    # Hard priority tiers — scoring only applies within the winning tier:
    #   1. existing planet (reuse current colony, no move needed)
    #   2. free new slot (remaining_slots > 0, no relocation)
    #   3. relocation (full-capacity char repurposing an existing colony)
    # This ensures free-capacity chars are exhausted before relocations happen,
    # even when same_corp / preferred_chars bonuses would otherwise tip the scale.
    existing_tier = [(p, s, ep, ir) for p, s, ep, ir in eligible if ep]
    new_tier      = [(p, s, ep, ir) for p, s, ep, ir in eligible if not ep and not ir]
    reloc_tier    = [(p, s, ep, ir) for p, s, ep, ir in eligible if not ep and ir]

    to_score = existing_tier or new_tier or reloc_tier

    best: tuple[tuple[int, int, int], dict[str, Any], dict[str, Any], bool] | None = None
    for planet, state, existing_planet, is_relocation in to_score:
        score = 0
        if state["id"] in preferred_chars:
            score += 200
        if state.get("same_corp_as_main"):
            score += 90
        if int(planet["system_id"]) in state["systems_with_colonies"]:
            score += 75
        if preferred_systems and int(planet["system_id"]) in preferred_systems:
            score += 40
        score += min(max(state["remaining_slots"], 0), 20)
        score -= state["new_assignments"]
        score -= state["existing_reuse_assignments"]
        if is_relocation:
            # Fill up one character's relocation slots before starting another.
            # +300 per existing relocation assignment dominates all other bonuses
            # (corp +90, preferred_chars +200), so once we commit to a char we
            # keep using them until their reloc_free hits 0.
            score += state["relocation_assignments"] * 300
        else:
            score -= state["relocation_assignments"]

        tiebreak = (score, state["remaining_slots"])
        if not best or tiebreak > best[0]:
            best = (tiebreak, planet, state, is_relocation)

    if not best:
        return None, None, False
    return best[1], best[2], best[3]


def _assignment_summary_text(
    assignments: list[dict[str, Any]],
    flows: list[dict[str, Any]],
    missing: list[dict[str, Any]],
) -> str:
    lines = ["Colony Assignment Planner", ""]
    lines.append("Assignments:")
    for item in assignments:
        detail_summary = " | ".join(item.get("detail_items") or [])
        lines.append(
            f"- {item['character_name']} | {item['system_name']} | {item['planet_name']} | {item['role']} | {detail_summary or item['summary']}"
        )
    if flows:
        lines.append("")
        lines.append("Material Flow:")
        for flow in flows:
            lines.append(
                f"- {flow['from_product']} -> {flow['to_product']} | {flow['transport_label']} | {flow['from_character']} -> {flow['to_character']}"
            )
    if missing:
        lines.append("")
        lines.append("Missing:")
        for item in missing:
            lines.append(f"- {item['kind']}: {item['label']}")
    return "\n".join(lines)


def _missing_label(item: dict[str, Any], lang: str, product_labels: dict[str, str]) -> str:
    kind = item.get("kind") or "item"
    label = item.get("label") or ""
    if kind == "system" and label == "single_system_unavailable":
        return translate("colony_plan.single_system_unavailable", lang=lang)
    if kind == "extractor":
        return translate("colony_plan.missing_extractor", lang=lang, label=product_labels.get(label, label))
    if kind == "factory":
        return translate("colony_plan.missing_factory", lang=lang, label=product_labels.get(label, label))
    return str(label)


def _missing_kind_label(kind: str, lang: str) -> str:
    mapping = {
        "system": "colony_plan.missing_kind_system",
        "extractor": "colony_plan.missing_kind_extractor",
        "factory": "colony_plan.missing_kind_factory",
    }
    return translate(mapping.get(kind, "common.unknown"), lang=lang, default=kind)


def _build_assignment_plan(
    *,
    product_name: str,
    selected_systems: list[dict],
    selected_characters: list[Character],
    all_colonies: list[dict],
    include_unassigned: bool,
    single_system_only: bool,
) -> dict[str, Any]:
    chain = _collect_chain(product_name)
    required_p0 = set(chain["p0_needed"])
    main_character = next((char for char in selected_characters if getattr(char, "is_main", False)), None)
    main_corporation = (getattr(main_character, "corporation_name", "") or "").strip().casefold()
    char_id_by_name = {char.character_name: char.id for char in selected_characters}
    selected_char_ids = {char.id for char in selected_characters}
    selected_colonies = [
        colony for colony in all_colonies
        if char_id_by_name.get(colony.get("character_name") or "") in selected_char_ids
    ]

    scoped_systems, mode_meta = _pick_system_subset(selected_systems, required_p0, selected_colonies, single_system_only)
    if single_system_only and not mode_meta.get("viable"):
        return {
            "chain": chain,
            "assignments": [],
            "flows": [],
            "missing": [{"kind": "system", "label": "single_system_unavailable"}],
            "characters": [],
            "system_mode": mode_meta,
            "selected_systems": selected_systems,
            "used_systems": [],
            "summary_text": "",
            "missing_planets": 0,
            "additional_characters_needed": 0,
        }

    used_system_ids = {system["id"] for system in scoped_systems}
    selected_colonies = [
        colony for colony in selected_colonies
        if int(colony.get("solar_system_id") or 0) in used_system_ids
    ]
    all_counts = Counter(colony.get("character_name") for colony in all_colonies if colony.get("character_name"))
    char_state: dict[int, dict[str, Any]] = {}
    for char in selected_characters:
        state = _character_capacity(char, int(all_counts.get(char.character_name, 0) or 0))
        state["same_corp_as_main"] = bool(
            main_corporation
            and ((getattr(char, "corporation_name", "") or "").strip().casefold() == main_corporation)
        )
        state["selected_system_existing"] = sum(
            1 for colony in selected_colonies if colony.get("character_name") == char.character_name
        )
        state["systems_with_colonies"] = {
            int(colony.get("solar_system_id") or 0)
            for colony in selected_colonies
            if colony.get("character_name") == char.character_name
        }
        state["new_assignments"] = 0
        state["existing_reuse_assignments"] = 0
        # Relocation = abandoning any existing colony (in any system) to free a slot.
        # Previously limited to colonies inside the selected systems; now any colony
        # can be abandoned so full chars can still plan new placements.
        state["relocation_slots"] = state["existing_total"]
        state["relocation_assignments"] = 0
        state["assignments"] = []
        char_state[char.id] = state

    planet_pool, occupied_by_selected, _blocked = _make_planet_pool(scoped_systems, selected_char_ids, all_colonies, char_id_by_name)
    feasibility = _feasibility_analysis(
        chain=chain,
        scoped_systems=scoped_systems,
        char_state=char_state,
        planet_pool=planet_pool,
        blocked_planets=_blocked,
        all_colonies=all_colonies,
        char_id_by_name=char_id_by_name,
        selected_char_ids=selected_char_ids,
    )
    assignments: list[dict[str, Any]] = []
    # Track (planet_id, char_id) pairs — different chars may share a planet.
    assigned_planet_chars: set[tuple[int, int]] = set()
    assigned_system_ids: set[int] = set()
    output_task_by_product: dict[str, dict[str, Any]] = {}
    missing: list[dict[str, Any]] = []
    covered_p0: set[str] = set()
    covered_factory: set[str] = set()

    def _commit_assignment(planet: dict, char: dict, is_reloc: bool) -> str:
        """Update char_state counters and return status string."""
        existing = planet.get("occupied_char_id") == char["id"]
        if existing:
            char["existing_reuse_assignments"] += 1
        elif is_reloc:
            char["relocation_assignments"] += 1
        else:
            char["remaining_slots"] -= 1
            char["new_assignments"] += 1
        char["assignments"].append(planet["planet_id"])
        assigned_planet_chars.add((int(planet["planet_id"]), char["id"]))
        assigned_system_ids.add(int(planet["system_id"]))
        return "existing" if existing else ("relocate" if is_reloc else "new")

    # ── Phase 1: Self-sufficient P2 assignments ──────────────────────────────
    # One planet runs two ECUs (P0_A + P0_B) and a P2 factory — 1 slot, 3 roles.
    for p2_name, ss in chain.get("self_sufficient_p2", {}).items():
        p0_a, p0_b = ss["p0_a"], ss["p0_b"]
        combined = [
            p for p in planet_pool
            if p0_a in p.get("resources", []) and p0_b in p.get("resources", [])
        ]
        chosen_planet, chosen_char, is_relocation = _select_assignment(
            candidates=combined,
            char_state=char_state,
            assigned_planet_chars=assigned_planet_chars,
            include_unassigned=include_unassigned,
            preferred_systems=assigned_system_ids,
        )
        if not chosen_planet or not chosen_char:
            continue  # Fall through to separate P0 + factory assignments

        status = _commit_assignment(chosen_planet, chosen_char, is_relocation)
        covered_p0.add(p0_a)
        covered_p0.add(p0_b)
        covered_factory.add(p2_name)

        p1_a, p1_b = ss["p1_a"], ss["p1_b"]
        entry = {
            "character_id": chosen_char["id"],
            "character_name": chosen_char["name"],
            "planet_id": chosen_planet["planet_id"],
            "planet_name": chosen_planet["planet_name"],
            "planet_type": chosen_planet["planet_type"],
            "system_id": chosen_planet["system_id"],
            "system_name": chosen_planet["system_name"],
            "role": "Extractor+Factory",
            "summary": f"{p0_a} + {p0_b} → {p2_name}",
            "detail_items": [p0_a, p0_b],
            "output_product": p2_name,
            "status": status,
            "status_label": status,
            "tier": "P2",
        }
        assignments.append(entry)
        # Both P1 outputs and the P2 itself route through this entry for P3 preferencing.
        output_task_by_product[p1_a] = entry
        output_task_by_product[p1_b] = entry
        output_task_by_product[p2_name] = entry

    # ── Phase 2: Remaining P0 extractors ─────────────────────────────────────
    for p0_name in chain["p0_needed"]:
        if p0_name in covered_p0:
            continue
        matching = [planet for planet in planet_pool if p0_name in planet["resources"]]
        chosen_planet, chosen_char, is_relocation = _select_assignment(
            candidates=matching,
            char_state=char_state,
            assigned_planet_chars=assigned_planet_chars,
            include_unassigned=include_unassigned,
            preferred_systems=assigned_system_ids,
        )
        if not chosen_planet or not chosen_char:
            missing.append({"kind": "extractor", "label": p0_name})
            continue

        status = _commit_assignment(chosen_planet, chosen_char, is_relocation)
        p1_output = P0_TO_P1.get(p0_name, "")
        entry = {
            "character_id": chosen_char["id"],
            "character_name": chosen_char["name"],
            "planet_id": chosen_planet["planet_id"],
            "planet_name": chosen_planet["planet_name"],
            "planet_type": chosen_planet["planet_type"],
            "system_id": chosen_planet["system_id"],
            "system_name": chosen_planet["system_name"],
            "role": "Extractor",
            "summary": p0_name,
            "detail_items": [p0_name],
            "output_product": p1_output,
            "status": status,
            "status_label": status,
            "tier": "P0",
        }
        assignments.append(entry)
        output_task_by_product[p1_output] = entry

    # ── Phase 3: Factory stages P2 → P4 (skip those handled by SS P2) ────────
    factory_products: list[str] = []
    for tier in ("P2", "P3", "P4"):
        factory_products.extend(chain["tiers"][tier])
    if chain["tier"] == "P1":
        factory_products = []

    for product in factory_products:
        if product in covered_factory:
            continue  # already produced by a self-sufficient P2 planet
        tier = _product_tier(product) or ""
        if tier == "P2":
            inputs = list(P1_TO_P2.get(product, []))
        elif tier == "P3":
            inputs = list(P2_TO_P3.get(product, []))
        else:
            inputs = list(P3_TO_P4.get(product, []))

        preferred_chars = {
            output_task_by_product[item]["character_id"]
            for item in inputs
            if item in output_task_by_product
        }
        # P4 Advanced Production Plants can only be built on Barren or Temperate planets.
        factory_candidates = (
            [p for p in planet_pool if p.get("planet_type") in ("Barren", "Temperate")]
            if tier == "P4"
            else planet_pool
        )
        chosen_planet, chosen_char, is_relocation = _select_assignment(
            candidates=factory_candidates,
            char_state=char_state,
            assigned_planet_chars=assigned_planet_chars,
            include_unassigned=include_unassigned,
            preferred_chars=preferred_chars,
            preferred_systems=assigned_system_ids,
        )
        if not chosen_planet or not chosen_char:
            missing.append({"kind": "factory", "label": product})
            continue

        status = _commit_assignment(chosen_planet, chosen_char, is_relocation)
        entry = {
            "character_id": chosen_char["id"],
            "character_name": chosen_char["name"],
            "planet_id": chosen_planet["planet_id"],
            "planet_name": chosen_planet["planet_name"],
            "planet_type": chosen_planet["planet_type"],
            "system_id": chosen_planet["system_id"],
            "system_name": chosen_planet["system_name"],
            "role": FACTORY_ANY_TYPE,
            "summary": f"{product} · " + " · ".join(inputs),
            "detail_items": inputs,
            "output_product": product,
            "status": status,
            "status_label": status,
            "tier": tier,
        }
        assignments.append(entry)
        output_task_by_product[product] = entry

    flow_items: list[dict[str, Any]] = []
    for source_name, target_name in chain["flow_edges"]:
        source_task = output_task_by_product.get(source_name)
        target_task = output_task_by_product.get(target_name)
        if not source_task or not target_task:
            continue
        same_character = source_task["character_id"] == target_task["character_id"]
        cross_system = source_task["system_id"] != target_task["system_id"]
        if same_character and not cross_system:
            transport_label = "same_character"
        elif cross_system:
            transport_label = "cross_system"
        else:
            transport_label = "customs_office"
        flow_items.append({
            "from_product": source_name,
            "to_product": target_name,
            "from_character": source_task["character_name"],
            "to_character": target_task["character_name"],
            "from_planet": source_task["planet_name"],
            "to_planet": target_task["planet_name"],
            "transport_label": transport_label,
            "same_character": same_character,
            "cross_system": cross_system,
        })

    assignments.sort(key=lambda item: (item["character_name"].casefold(), item["system_name"].casefold(), item["planet_name"].casefold(), item["role"]))
    char_rows = sorted(char_state.values(), key=lambda item: item["name"].casefold())
    unused_characters: list[dict[str, Any]] = []
    for item in char_rows:
        item["used_in_plan"] = len(item.get("assignments") or [])
        reason = None
        if item["used_in_plan"] <= 0:
            if item["remaining_slots"] <= 0:
                reason = "no_slots"
            elif (not include_unassigned) and item["selected_system_existing"] <= 0:
                reason = "excluded_unassigned"
            else:
                reason = "not_needed"
            unused_characters.append({
                "id": item["id"],
                "name": item["name"],
                "portrait": item.get("portrait"),
                "existing_total": item["existing_total"],
                "max_planets": item["max_planets"],
                "reason": reason,
            })
    missing_planets = len(missing)
    additional_characters_needed = ceil(missing_planets / 6) if missing_planets else 0

    return {
        "chain": chain,
        "assignments": assignments,
        "flows": flow_items,
        "missing": missing,
        "characters": char_rows,
        "unused_characters": unused_characters,
        "system_mode": mode_meta,
        "selected_systems": selected_systems,
        "used_systems": scoped_systems,
        "summary_text": _assignment_summary_text(assignments, flow_items, missing),
        "missing_planets": missing_planets,
        "additional_characters_needed": additional_characters_needed,
        "feasibility": feasibility,
    }


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def colony_plan_page(
    request: Request,
    product: str | None = None,
    system_ids: str | None = Query(default=None),
    character_ids: list[int] | None = Query(default=None),
    all_characters: int = Query(default=1),
    single_system: int = Query(default=0),
    include_unassigned: int = Query(default=1),
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    lang = get_language_from_request(request)
    characters = db.query(Character).filter(Character.account_id == account.id).all()
    _attach_pi_skills(characters, db)
    all_colonies = _load_cached_colonies(account, characters, db) if characters else []
    all_counts = Counter(colony.get("character_name") for colony in all_colonies if colony.get("character_name"))

    product_labels = _build_product_labels(lang)
    planet_type_labels = _build_planet_type_labels(lang)

    all_products = []
    for tier, names in (("P1", ALL_P1), ("P2", ALL_P2), ("P3", ALL_P3), ("P4", ALL_P4)):
        for name in names:
            all_products.append({
                "name": name,
                "display_name": product_labels.get(name, name),
                "tier": tier,
            })
    all_products.sort(key=lambda item: (TIER_ORDER[item["tier"]], item["display_name"].casefold()))

    selected_system_ids = _parse_csv_ints(system_ids)
    selected_character_ids = set(character_ids or [])
    use_all_characters = bool(all_characters) or not selected_character_ids
    selected_characters = characters if use_all_characters else [char for char in characters if char.id in selected_character_ids]
    selected_character_ids = {char.id for char in selected_characters}
    character_capacity_rows = []
    character_capacity_map: dict[int, dict[str, Any]] = {}
    for char in characters:
        capacity = _character_capacity(char, int(all_counts.get(char.character_name, 0) or 0))
        character_capacity_rows.append(capacity)
        character_capacity_map[char.id] = capacity

    selected_systems: list[dict[str, Any]] = []
    for system_id in selected_system_ids:
        planets = _load_system_planets(system_id)
        if not planets:
            continue
        meta = _system_meta(system_id)
        selected_systems.append({
            **meta,
            "planets": planets,
            "planet_count": len(planets),
            "planet_types": sorted({planet["planet_type"] for planet in planets}),
        })

    plan = None
    plan_error = None
    valid_product_names = {item["name"] for item in all_products}
    if product and product not in valid_product_names:
        plan_error = translate("colony_plan.invalid_product", lang=lang)
    elif product and selected_systems and selected_characters:
        plan = _build_assignment_plan(
            product_name=product,
            selected_systems=selected_systems,
            selected_characters=selected_characters,
            all_colonies=all_colonies,
            include_unassigned=bool(include_unassigned),
            single_system_only=bool(single_system),
        )
        for item in plan["assignments"]:
            item["summary_display"] = " | ".join(product_labels.get(part, part) for part in item["detail_items"])
            item["output_display"] = product_labels.get(item["output_product"], item["output_product"])
        for flow in plan["flows"]:
            flow["from_product_display"] = product_labels.get(flow["from_product"], flow["from_product"])
            flow["to_product_display"] = product_labels.get(flow["to_product"], flow["to_product"])
        for item in plan["missing"]:
            item["display_label"] = _missing_label(item, lang, product_labels)
            item["kind_display"] = _missing_kind_label(item["kind"], lang)

    return templates.TemplateResponse("colony_plan.html", {
        "request": request,
        "account": account,
        "all_products": all_products,
        "product_labels": product_labels,
        "planet_type_labels": planet_type_labels,
        "selected_product": product,
        "selected_product_display": product_labels.get(product, product) if product else None,
        "selected_system_ids": selected_system_ids,
        "selected_systems": selected_systems,
        "characters": characters,
        "character_capacity_rows": character_capacity_rows,
        "character_capacity_map": character_capacity_map,
        "selected_character_ids": selected_character_ids,
        "use_all_characters": use_all_characters,
        "single_system": bool(single_system),
        "include_unassigned": bool(include_unassigned),
        "plan": plan,
        "plan_error": plan_error,
    })
