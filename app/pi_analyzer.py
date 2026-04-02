"""
PlanetFlow System-Analyzer - Analysiert mögliche PI-Produktionsketten
"""
from app.pi_data import (
    PLANET_RESOURCES, P0_TO_P1, P1_TO_P2, P2_TO_P3, P3_TO_P4,
    PLANET_TYPE_COLORS
)


def _all_p1_for_product(name: str, tier: str) -> list[str]:
    """Alle benötigten P1-Inputs (flach, dedupliziert) für ein Produkt."""
    if tier == "P1":
        return [name]
    if tier == "P2":
        return list(P1_TO_P2.get(name, []))
    if tier == "P3":
        p1s: set[str] = set()
        for p2 in P2_TO_P3.get(name, []):
            p1s.update(P1_TO_P2.get(p2, []))
        return list(p1s)
    if tier == "P4":
        p1s = set()
        for inp in P3_TO_P4.get(name, []):
            if inp in P2_TO_P3:          # P3-Input
                for p2 in P2_TO_P3[inp]:
                    p1s.update(P1_TO_P2.get(p2, []))
            elif inp in P1_TO_P2:        # P2-Input (Sonderfall)
                p1s.update(P1_TO_P2[inp])
            else:                         # Direkter P1-Input (Reactive Metals, Bacteria, Water)
                p1s.add(inp)
        return list(p1s)
    return []


def _p0_for_p1(p1_name: str) -> str | None:
    for p0, out in P0_TO_P1.items():
        if out == p1_name:
            return p0
    return None


def _single_planet_types_for_p1_inputs(p1_inputs: list[str], available_planets: list[str]) -> list[str]:
    required_p0 = {p0 for p0 in (_p0_for_p1(p1) for p1 in p1_inputs) if p0}
    if not required_p0:
        return []
    matches: list[str] = []
    for pt in available_planets:
        resources = set(PLANET_RESOURCES.get(pt, []))
        if required_p0.issubset(resources):
            matches.append(pt)
    return sorted(set(matches))


def analyze_system(planet_types: list[str]) -> list[dict]:
    """
    Analysiert ein System und gibt empfohlene PI-Produktionsketten zurück.

    Args:
        planet_types: Liste der Planetentypen im System

    Returns:
        Sortierte Liste mit möglichen Produkten und deren Bewertung
    """
    results = []

    # Schritt 1: Alle verfügbaren P0-Ressourcen sammeln
    available_p0: set[str] = set()
    for pt in planet_types:
        resources = PLANET_RESOURCES.get(pt, [])
        available_p0.update(resources)

    # Schritt 2: Verfügbare P1-Produkte bestimmen
    available_p1: set[str] = set()
    for p0, p1 in P0_TO_P1.items():
        if p0 in available_p0:
            available_p1.add(p1)

    # P1-Produkte zur Ergebnisliste hinzufügen
    for p1 in sorted(available_p1):
        p0_inputs = [p0 for p0, out in P0_TO_P1.items() if out == p1]
        needed_planets = _planets_for_p0(p0_inputs[0] if p0_inputs else "")
        results.append({
            "name": p1,
            "tier": "P1",
            "inputs": p0_inputs[:1],
            "planets_needed": needed_planets,
            "single_planet_types": _single_planet_types_for_p1_inputs([p1], planet_types),
            "single_planet_viable": len(_single_planet_types_for_p1_inputs([p1], planet_types)) > 0,
            "all_p1_inputs": [p1],
            "available": True,
            "score": 10,
        })

    # Schritt 3: Verfügbare P2-Produkte bestimmen
    available_p2: set[str] = set()
    for p2, inputs in P1_TO_P2.items():
        if all(inp in available_p1 for inp in inputs):
            available_p2.add(p2)

    for p2 in sorted(available_p2):
        inputs = P1_TO_P2.get(p2, [])
        needed_planets = _planets_for_p1_list(inputs, planet_types)
        results.append({
            "name": p2,
            "tier": "P2",
            "inputs": inputs,
            "planets_needed": needed_planets,
            "single_planet_types": _single_planet_types_for_p1_inputs(inputs, planet_types),
            "single_planet_viable": len(_single_planet_types_for_p1_inputs(inputs, planet_types)) > 0,
            "all_p1_inputs": _all_p1_for_product(p2, "P2"),
            "available": True,
            "score": 25,
        })

    # Schritt 4: Verfügbare P3-Produkte bestimmen
    available_p3: set[str] = set()
    for p3, inputs in P2_TO_P3.items():
        if all(inp in available_p2 for inp in inputs):
            available_p3.add(p3)

    for p3 in sorted(available_p3):
        inputs = P2_TO_P3.get(p3, [])
        needed_planets: set[str] = set()
        for p2 in inputs:
            for pt in _planets_for_p1_list(P1_TO_P2.get(p2, []), planet_types):
                needed_planets.add(pt)
        single_planet_types = _single_planet_types_for_p1_inputs(_all_p1_for_product(p3, "P3"), planet_types)
        results.append({
            "name": p3,
            "tier": "P3",
            "inputs": inputs,
            "planets_needed": sorted(needed_planets),
            "single_planet_types": single_planet_types,
            "single_planet_viable": len(single_planet_types) > 0,
            "all_p1_inputs": _all_p1_for_product(p3, "P3"),
            "available": True,
            "score": 60,
        })

    # Schritt 5: P4-Produkte (nur auf Barren oder Temperate)
    # Drei P4-Produkte haben einen P1-Input (Reactive Metals, Bacteria, Water)
    has_advanced_planet = any(pt in ("Barren", "Temperate") for pt in planet_types)
    if has_advanced_planet:
        for p4, inputs in P3_TO_P4.items():
            if all(inp in available_p3 or inp in available_p1 for inp in inputs):
                needed_planets = set()
                for p3_inp in inputs:
                    for p2 in P2_TO_P3.get(p3_inp, []):
                        for pt in _planets_for_p1_list(P1_TO_P2.get(p2, []), planet_types):
                            needed_planets.add(pt)
                for advanced in ("Barren", "Temperate"):
                    if advanced in planet_types:
                        needed_planets.add(advanced)
                single_planet_types = _single_planet_types_for_p1_inputs(_all_p1_for_product(p4, "P4"), planet_types)
                results.append({
                    "name": p4,
                    "tier": "P4",
                    "inputs": inputs,
                    "planets_needed": sorted(needed_planets),
                    "single_planet_types": single_planet_types,
                    "single_planet_viable": len(single_planet_types) > 0,
                    "all_p1_inputs": _all_p1_for_product(p4, "P4"),
                    "available": True,
                    "score": 150,
                })

    # Nach Score sortieren (absteigend), dann nach Tier (absteigend), dann Name
    tier_order = {"P4": 4, "P3": 3, "P2": 2, "P1": 1}
    results.sort(key=lambda x: (tier_order.get(x["tier"], 0), x["score"]), reverse=True)

    return results


def _planets_for_p0(p0: str) -> list[str]:
    """Gibt Planetentypen zurück, auf denen eine P0-Ressource vorkommt."""
    return [
        pt for pt, resources in PLANET_RESOURCES.items()
        if p0 in resources
    ]


def _planets_for_p1_list(p1_inputs: list[str], available_planets: list[str]) -> list[str]:
    """Bestimmt welche Planetentypen für eine P1-Liste benötigt werden."""
    needed = set()
    for p1 in p1_inputs:
        for p0, out in P0_TO_P1.items():
            if out == p1:
                for pt in _planets_for_p0(p0):
                    if pt in available_planets:
                        needed.add(pt)
                        break
    return list(needed)
