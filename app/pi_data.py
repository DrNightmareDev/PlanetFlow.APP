"""
EVE Online Planetary Industry - Vollständige Produktionsdaten
Quelle: https://alysii.com/eve/pi/
"""

# P0 Rohstoffe → P1 Basisprodukte
P0_TO_P1: dict[str, str] = {
    "Aqueous Liquids":   "Water",
    "Autotrophs":        "Industrial Fibers",
    "Base Metals":       "Reactive Metals",
    "Carbon Compounds":  "Biofuels",
    "Complex Organisms": "Proteins",
    "Felsic Magma":      "Silicon",
    "Heavy Metals":      "Toxic Metals",
    "Ionic Solutions":   "Electrolytes",
    "Micro Organisms":   "Bacteria",
    "Noble Gas":         "Oxygen",
    "Noble Metals":      "Precious Metals",
    "Non-CS Crystals":   "Chiral Structures",
    "Planktic Colonies": "Biomass",
    "Reactive Gas":      "Oxidizing Compound",
    "Suspended Plasma":  "Plasmoids",
}

# P1 + P1 → P2 Raffinierte Produkte (je 40 P1 → 5 P2 pro Zyklus)
P1_TO_P2: dict[str, list[str]] = {
    "Biocells":                      ["Biofuels",          "Precious Metals"],
    "Construction Blocks":           ["Reactive Metals",   "Toxic Metals"],
    "Consumer Electronics":          ["Toxic Metals",      "Chiral Structures"],
    "Coolant":                       ["Electrolytes",      "Water"],
    "Enriched Uranium":              ["Precious Metals",   "Toxic Metals"],
    "Fertilizer":                    ["Bacteria",          "Proteins"],
    "Genetically Enhanced Livestock":["Proteins",          "Biomass"],
    "Livestock":                     ["Proteins",          "Biofuels"],
    "Mechanical Parts":              ["Reactive Metals",   "Precious Metals"],
    "Microfiber Shielding":          ["Industrial Fibers", "Silicon"],
    "Miniature Electronics":         ["Chiral Structures", "Silicon"],
    "Nanites":                       ["Bacteria",          "Reactive Metals"],
    "Oxides":                        ["Oxidizing Compound","Oxygen"],
    "Polyaramids":                   ["Oxidizing Compound","Industrial Fibers"],
    "Polytextiles":                  ["Biofuels",          "Industrial Fibers"],
    "Rocket Fuel":                   ["Plasmoids",         "Electrolytes"],
    "Silicate Glass":                ["Oxidizing Compound","Silicon"],
    "Superconductors":               ["Plasmoids",         "Water"],
    "Supertensile Plastics":         ["Oxygen",            "Biomass"],
    "Synthetic Oil":                 ["Electrolytes",      "Oxygen"],
    "Test Cultures":                 ["Bacteria",          "Water"],
    "Transmitter":                   ["Plasmoids",         "Chiral Structures"],
    "Viral Agent":                   ["Bacteria",          "Biomass"],
    "Water-Cooled CPU":              ["Reactive Metals",   "Water"],
}

# P2 → P3 Spezialisierte Produkte (je 10 P2 → 3 P3 pro Zyklus)
P2_TO_P3: dict[str, list[str]] = {
    "Biotech Research Reports":      ["Nanites",              "Livestock",                 "Construction Blocks"],
    "Camera Drones":                 ["Silicate Glass",        "Rocket Fuel"],
    "Condensates":                   ["Oxides",                "Coolant"],
    "Cryoprotectant Solution":       ["Test Cultures",         "Synthetic Oil",             "Fertilizer"],
    "Data Chips":                    ["Supertensile Plastics", "Microfiber Shielding"],
    "Gel-Matrix Biopaste":           ["Biocells",              "Oxides",                    "Superconductors"],
    "Guidance Systems":              ["Water-Cooled CPU",      "Transmitter"],
    "Hazmat Detection Systems":      ["Polytextiles",          "Viral Agent",               "Transmitter"],
    "Hermetic Membranes":            ["Polyaramids",           "Genetically Enhanced Livestock"],
    "High-Tech Transmitters":        ["Polyaramids",           "Transmitter"],
    "Industrial Explosives":         ["Fertilizer",            "Polytextiles"],
    "Neocoms":                       ["Biocells",              "Silicate Glass"],
    "Nuclear Reactors":              ["Microfiber Shielding",  "Enriched Uranium"],
    "Planetary Vehicles":            ["Supertensile Plastics", "Mechanical Parts",          "Miniature Electronics"],
    "Robotics":                      ["Mechanical Parts",      "Consumer Electronics"],
    "Smartfab Units":                ["Construction Blocks",   "Miniature Electronics"],
    "Supercomputers":                ["Water-Cooled CPU",      "Coolant",                   "Consumer Electronics"],
    "Synthetic Synapses":            ["Supertensile Plastics", "Test Cultures"],
    "Transcranial Microcontrollers": ["Biocells",              "Nanites"],
    "Ukomi Super Conductors":        ["Synthetic Oil",         "Superconductors"],
    "Vaccines":                      ["Livestock",             "Viral Agent"],
}

# P3 → P4 Hochentwickelte Produkte (nur auf Barren oder Temperate)
# Nano-Factory, Organic Mortar Applicators und Sterile Conduits benötigen
# zusätzlich ein P1-Produkt als dritten Input (Reactive Metals / Bacteria / Water).
P3_TO_P4: dict[str, list[str]] = {
    "Broadcast Node":              ["Neocoms",             "Data Chips",                "High-Tech Transmitters"],
    "Integrity Response Drones":   ["Gel-Matrix Biopaste", "Hazmat Detection Systems",  "Planetary Vehicles"],
    "Nano-Factory":                ["Industrial Explosives","Ukomi Super Conductors",    "Reactive Metals"],
    "Organic Mortar Applicators":  ["Condensates",          "Robotics",                  "Bacteria"],
    "Recursive Computing Module":  ["Synthetic Synapses",   "Guidance Systems",          "Transcranial Microcontrollers"],
    "Self-Harmonizing Power Core": ["Camera Drones",        "Nuclear Reactors",          "Hermetic Membranes"],
    "Sterile Conduits":            ["Smartfab Units",       "Vaccines",                  "Water"],
    "Wetware Mainframe":           ["Supercomputers",       "Biotech Research Reports",  "Cryoprotectant Solution"],
}

# P0 Rohstoffe pro Planetentyp (je 5 Ressourcen pro Planet)
PLANET_RESOURCES: dict[str, list[str]] = {
    "Barren":    ["Aqueous Liquids", "Base Metals",    "Carbon Compounds", "Micro Organisms",   "Noble Metals"],
    "Gas":       ["Aqueous Liquids", "Base Metals",    "Ionic Solutions",  "Noble Gas",          "Reactive Gas"],
    "Ice":       ["Aqueous Liquids", "Heavy Metals",   "Micro Organisms",  "Noble Gas",          "Planktic Colonies"],
    "Lava":      ["Base Metals",     "Felsic Magma",   "Heavy Metals",     "Non-CS Crystals",    "Suspended Plasma"],
    "Oceanic":   ["Aqueous Liquids", "Carbon Compounds","Complex Organisms","Micro Organisms",   "Planktic Colonies"],
    "Plasma":    ["Base Metals",     "Heavy Metals",   "Noble Metals",     "Non-CS Crystals",    "Suspended Plasma"],
    "Storm":     ["Aqueous Liquids", "Base Metals",    "Ionic Solutions",  "Noble Gas",          "Suspended Plasma"],
    "Temperate": ["Aqueous Liquids", "Autotrophs",     "Carbon Compounds", "Complex Organisms",  "Micro Organisms"],
}

# Vollständige Produktionslisten
ALL_P1 = sorted(set(P0_TO_P1.values()))
ALL_P2 = sorted(P1_TO_P2.keys())
ALL_P3 = sorted(P2_TO_P3.keys())
ALL_P4 = sorted(P3_TO_P4.keys())

# Planetentyp-Farben für UI
PLANET_TYPE_COLORS: dict[str, str] = {
    "Storm":     "#5b8de4",
    "Barren":    "#a67c52",
    "Gas":       "#7fb069",
    "Lava":      "#e63946",
    "Oceanic":   "#2980b9",
    "Plasma":    "#9b59b6",
    "Temperate": "#27ae60",
    "Ice":       "#74b9ff",
}
