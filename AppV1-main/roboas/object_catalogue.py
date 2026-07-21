# object_catalogue.py
"""
Single source of truth for object dimensions in meters.
"""

OBJECT_CATALOGUE = {
    "black marker": {
        "length_m": 0.134,
        "width_m": 0.02053,
        "breadth_m": 0.02053,
        "height_m": 0.02053,
    },
    "blue cube": {
        "length_m": 0.040,
        "width_m": 0.036,
        "breadth_m": 0.036,
        "height_m": 0.040,
    },
    "red cube": {
        "length_m": 0.040,
        "width_m": 0.036,
        "breadth_m": 0.036,
        "height_m": 0.040,
    },
    "green cube": {
        "length_m": 0.040,
        "width_m": 0.036,
        "breadth_m": 0.036,
        "height_m": 0.040,
    },
    "yellow cube": {
        "length_m": 0.040,
        "width_m": 0.036,
        "breadth_m": 0.036,
        "height_m": 0.040,
    },
    "cube": {
        "length_m": 0.040,
        "width_m": 0.036,
        "breadth_m": 0.036,
        "height_m": 0.040,
    },
    "medicine": {
        "length_m": 0.11572,
        "width_m": 0.05117,
        "breadth_m": 0.05117,
        "height_m": 0.01895,
    },
    "nut": {
        "length_m": 0.0346,
        "width_m": 0.030,
        "breadth_m": 0.020,
        "height_m": 0.017,
    },
    "sponge": {
        "length_m": 0.11258,
        "width_m": 0.080,
        "breadth_m": 0.0154,
        "height_m": 0.0154,
    },
    "pipe": {
        "length_m": 0.120,
        "width_m": 0.110,
        "breadth_m": 0.0545,
        "height_m": 0.0545,
        "grasp_length_m": 0.0567,
        "grasp_width_m": 0.040,
        "grasp_height_m": 0.040,
    },
    "screwdriver": {
        "length_m": 0.104,
        "width_m": 0.025,
        "breadth_m": 0.025,
        "height_m": 0.025,
    },
    "soy milk": {
        "length_m": 0.030,
        "width_m": 0.030,
        "breadth_m": 0.030,
        "height_m": 0.030,
    },
    "umbrella": {
        "length_m": 0.030,
        "width_m": 0.030,
        "breadth_m": 0.030,
        "height_m": 0.030,
    },
    "wrench": {
        "length_m": 0.030,
        "width_m": 0.030,
        "breadth_m": 0.030,
        "height_m": 0.030,
    },
    "hat": {
        "length_m": 0.030,
        "width_m": 0.030,
        "breadth_m": 0.030,
        "height_m": 0.030,
    }
}

GRASP_OFFSETS = {
    "black marker": {"x": 0.0, "y": 0.0, "z": 0.0},
    "blue cube":    {"x": 0.0, "y": 0.0, "z": 0.0},
    "red cube":     {"x": 0.0, "y": 0.0, "z": 0.0},
    "green cube":   {"x": 0.0, "y": 0.0, "z": 0.0},
    "yellow cube":  {"x": 0.0, "y": 0.0, "z": 0.0},
    "cube":         {"x": 0.0, "y": 0.0, "z": 0.0},
    "medicine":     {"x": 0.0, "y": 0.0, "z": 0.0},
    "nut":          {"x": 0.0, "y": 0.0, "z": 0.0},
    "sponge":       {"x": 0.0, "y": 0.0, "z": 0.0},
    "pipe":         {"x": 0.0, "y": -0.030, "z": 0.0},
    "screwdriver":  {"x": 0.0, "y": 0.0, "z": 0.0},
    "soy milk":     {"x": 0.0, "y": 0.0, "z": 0.0},
    "umbrella":     {"x": 0.0, "y": 0.0, "z": 0.0},
    "wrench":       {"x": 0.0, "y": 0.0, "z": 0.0},
    "hat":          {"x": 0.0, "y": 0.0, "z": 0.0},
}
