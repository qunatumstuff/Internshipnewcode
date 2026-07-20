# object_catalogue.py
"""
Single source of truth for object dimensions in meters.
"""

OBJECT_CATALOGUE = {
    "cube": {
        "length_m": 0.040,
        "width_m": 0.040,
        "breadth_m": 0.040,
        "height_m": 0.040,
        "offset_x": 0.0,
        "offset_y": 0.0,
        "offset_z": 0.0,
    },
    "medicine": {
        "length_m": 0.11572,
        "width_m": 0.05117,
        "breadth_m": 0.05117,
        "height_m": 0.01895,
        "offset_x": 0.0,
        "offset_y": 0.0,
        "offset_z": 0.0,
    },
    "nut": {
        "length_m": 0.0346,
        "width_m": 0.030,
        "breadth_m": 0.020,
        "height_m": 0.017,
        "offset_x": 0.0,
        "offset_y": 0.0,
        "offset_z": 0.0,
    },
    "sponge": {
        "length_m": 0.11258,
        "width_m": 0.080,
        "breadth_m": 0.0154,
        "height_m": 0.0154,
        "offset_x": 0.0,
        "offset_y": 0.0,
        "offset_z": 0.0,
    },
    "pipe": {
        "length_m": 0.120,
        "width_m": 0.110,
        "breadth_m": 0.0545,
        "height_m": 0.0545,
        "offset_x": 0.0,
        "offset_y": -0.030,
        "offset_z": 0.0,
    },
}

GRASP_OFFSETS = {
    "cube":     {"x": 0.0, "y": 0.0,   "z": 0.0},
    "medicine": {"x": 0.0, "y": 0.0,   "z": 0.0},
    "nut":      {"x": 0.0, "y": 0.0,   "z": 0.0},
    "sponge":   {"x": 0.0, "y": 0.0,   "z": 0.0},
    "pipe":     {"x": 0.0, "y": -0.030,"z": 0.0},
}
