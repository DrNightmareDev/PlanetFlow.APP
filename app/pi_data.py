"""
EVE Online Planetary Industry - Vollständige Produktionsdaten
"""

# P0 Rohstoffe pro Planetentyp
PLANET_RESOURCES: dict[str, list[str]] = {
    "Barren": [
        "Base Metals", "Carbon Compounds", "Microorganisms",
        "Noble Metals", "Chiral Structures"
    ],
    "Gas": [
        "Aqueous Liquids", "Ionic Solutions", "Reactive Gas",
        "Noble Gas", "Suspended Plasma"
    ],
    "Ice": [
        "Aqueous Liquids", "Heavy Metals", "Suspended Plasma", "Microorganisms"
    ],
    "Lava": [
        "Felsic Magma", "Suspended Plasma", "Heavy Metals",
        "Non-CS Crystals", "Ionic Solutions"
    ],
    "Oceanic": [
        "Aqueous Liquids", "Complex Organisms", "Carbon Compounds",
        "Planktic Colonies", "Microorganisms"
    ],
    "Plasma": [
        "Suspended Plasma", "Plasmoids", "Noble Gas",
        "Felsic Magma", "Non-CS Crystals"
    ],
    "Storm": [
        "Aqueous Liquids", "Ionic Solutions", "Suspended Plasma",
        "Reactive Gas", "Oxygen"
    ],
    "Temperate": [
        "Aqueous Liquids", "Carbon Compounds", "Autotrophs",
        "Proteins", "Complex Organisms", "Micro Organisms"
    ],
}

# P1 Basisprodukte (3000 P0 → 20 P1 pro Zyklus)
P0_TO_P1: dict[str, str] = {
    "Base Metals": "Reactive Metals",
    "Carbon Compounds": "Biofuels",
    "Microorganisms": "Bacteria",
    "Micro Organisms": "Bacteria",
    "Noble Metals": "Precious Metals",
    "Chiral Structures": "Chiral Structures",
    "Aqueous Liquids": "Water",
    "Ionic Solutions": "Electrolytes",
    "Reactive Gas": "Oxidizing Compound",
    "Noble Gas": "Oxygen",
    "Suspended Plasma": "Plasmoids",
    "Heavy Metals": "Toxic Metals",
    "Non-CS Crystals": "Silicon",
    "Felsic Magma": "Silicates",
    "Planktic Colonies": "Biomass",
    "Autotrophs": "Biofuels",
    "Proteins": "Proteins",
    "Complex Organisms": "Complex Organisms",
    "Plasmoids": "Plasmoids",
    "Oxygen": "Oxygen",
}

# P2 Raffinierte Produkte (je 40 P1 → 5 P2 pro Zyklus)
P1_TO_P2: dict[str, list[str]] = {
    "Biocells": ["Biofuels", "Precious Metals"],
    "Construction Blocks": ["Reactive Metals", "Toxic Metals"],
    "Consumer Electronics": ["Chiral Structures", "Toxic Metals"],
    "Coolant": ["Electrolytes", "Water"],
    "Enriched Uranium": ["Precious Metals", "Toxic Metals"],
    "Fertilizer": ["Bacteria", "Proteins"],
    "Genetically Enhanced Livestock": ["Bacteria", "Proteins"],
    "Guidance Systems": ["Chiral Structures", "Silicon"],
    "Hazmat Detection Systems": ["Bacteria", "Chiral Structures"],
    "Hermetic Membranes": ["Biomass", "Silicates"],
    "High-Tech Transmitters": ["Chiral Structures", "Plasmoids"],
    "Industrial Explosives": ["Oxidizing Compound", "Silicates"],
    "Neocoms": ["Biofuels", "Silicon"],
    "Nuclear Reactors": ["Enriched Uranium", "Toxic Metals"],
    "Planetary Vehicles": ["Biofuels", "Reactive Metals"],
    "Polytextiles": ["Biomass", "Biofuels"],
    "Protective Membrane Systems": ["Silicates", "Biofuels"],
    "Rocket Fuel": ["Electrolytes", "Plasmoids"],
    "Silicate Glass": ["Oxygen", "Silicon"],
    "Smartfab Units": ["Reactive Metals", "Silicon"],
    "Super Conductors": ["Electrolytes", "Plasmoids"],
    "Synthetic Oil": ["Electrolytes", "Oxygen"],
    "Transmitters": ["Chiral Structures", "Silicates"],
    "Viral Agent": ["Bacteria", "Biomass"],
    "Water-Cooled CPU": ["Reactive Metals", "Water"],
}

# P3 Spezialisierte Produkte (je 3× P2 → 1 P3 pro Zyklus)
P2_TO_P3: dict[str, list[str]] = {
    "Biotech Research Reports": ["Construction Blocks", "Viral Agent", "Water-Cooled CPU"],
    "Camera Drones": ["Consumer Electronics", "Rocket Fuel", "Silicate Glass"],
    "Condensates": ["Coolant", "Oxides", "Silicate Glass"],
    "Cryoprotectant Solution": ["Fertilizer", "Synthetic Oil", "Water-Cooled CPU"],
    "Data Chips": ["Consumer Electronics", "High-Tech Transmitters", "Microfiber Shielding"],
    "Gel-Matrix Biopaste": ["Biocells", "Oxides", "Silicate Glass"],
    "Guidance Systems": ["Guidance Systems", "Transmitters", "Enriched Uranium"],
    "Hazmat Detection Systems": ["Hazmat Detection Systems", "Silicate Glass", "Viral Agent"],
    "Hermetic Membranes": ["Hermetic Membranes", "Silicate Glass", "Transmitters"],
    "High-Tech Small Arms": ["Consumer Electronics", "Enriched Uranium", "Guidance Systems"],
    "Industrial Explosives": ["Industrial Explosives", "Silicate Glass", "Transmitters"],
    "Neocoms": ["Neocoms", "Silicon", "Transmitters"],
    "Nuclear Reactors": ["Enriched Uranium", "Nuclear Reactors", "Transmitters"],
    "Planetary Vehicles": ["Construction Blocks", "Guidance Systems", "Planetary Vehicles"],
    "Robotics": ["Consumer Electronics", "Mechanical Parts", "Transmitters"],
    "Rocket Fuel": ["Electrolytes", "Plasmoids", "Rocket Fuel"],
    "Silicate Glass": ["Oxygen", "Silicon", "Silicate Glass"],
    "Smartfab Units": ["Construction Blocks", "Guidance Systems", "Smartfab Units"],
    "Supercomputers": ["Consumer Electronics", "Coolant", "Water-Cooled CPU"],
    "Synthetic Synapses": ["Biocells", "Neocoms", "Super Conductors"],
    "Transcranial Microcontrollers": ["Biocells", "Nanites", "Silicate Glass"],
    "Ukomi Super Conductors": ["Silicates", "Super Conductors", "Synthetic Oil"],
    "Vaccines": ["Fertilizer", "Viral Agent", "Water-Cooled CPU"],
    "Mechanical Parts": ["Construction Blocks", "Planetary Vehicles", "Reactive Metals"],
    "Oxides": ["Oxidizing Compound", "Silicates", "Toxic Metals"],
    "Nanites": ["Bacteria", "Reactive Metals", "Water"],
    "Microfiber Shielding": ["Biofuels", "Silicates", "Silicon"],
}

# P4 Hochentwickelte Produkte (nur auf Barren oder Temperate) (3× P3 → 1 P4)
P3_TO_P4: dict[str, list[str]] = {
    "Broadcast Node": ["Neocoms", "Photovolatic Cells", "Recursive Computing Module"],
    "Integrity Response Drones": ["Gel-Matrix Biopaste", "Hazmat Detection Systems", "Planetary Vehicles"],
    "Nano-Factory": ["Industrial Explosives", "Reactive Metals", "Ukomi Super Conductors"],
    "Organic Mortar Applicators": ["Condensates", "Fertilizer", "Planetary Vehicles"],
    "Recursive Computing Module": ["Guidance Systems", "Photovolatic Cells", "Transmitters"],
    "Self-Harmonizing Power Core": ["Camera Drones", "Condensates", "Hermetic Membranes"],
    "Sterile Conduits": ["Condensates", "Robotics", "Vaccines"],
    "Wetware Mainframe": ["Biotech Research Reports", "Cryoprotectant Solution", "Supercomputers"],
}

# Vollständige Produktionsliste für PI-Analyzer
ALL_P1 = list(set(P0_TO_P1.values()))
ALL_P2 = list(P1_TO_P2.keys())
ALL_P3 = list(P2_TO_P3.keys())
ALL_P4 = list(P3_TO_P4.keys())

# Planetentyp-Farben für UI
PLANET_TYPE_COLORS: dict[str, str] = {
    "Storm": "#5b8de4",
    "Barren": "#a67c52",
    "Gas": "#7fb069",
    "Lava": "#e63946",
    "Oceanic": "#2980b9",
    "Plasma": "#9b59b6",
    "Temperate": "#27ae60",
    "Ice": "#74b9ff",
}
