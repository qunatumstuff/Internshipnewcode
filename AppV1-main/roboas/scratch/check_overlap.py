import math

OBJECT_CATALOGUE = {
    "black marker": {"size": "134 x 20.53 x 20.53 mm", "height_m": 0.02053,
                     "length_m": 0.134,   "breadth_m": 0.02053},
    "blue marker":  {"size": "134 x 20.53 x 20.53 mm", "height_m": 0.02053,
                     "length_m": 0.134,   "breadth_m": 0.02053},
    "cube":         {"size": "40 x 40 x 40 mm",         "height_m": 0.040,
                     "length_m": 0.040,   "breadth_m": 0.040},
    "green marker": {"size": "134 x 20.53 x 20.53 mm",  "height_m": 0.02053,
                     "length_m": 0.134,   "breadth_m": 0.02053},
    "medicine":     {"size": "115.72 x 51.17 x 18.95 mm","height_m": 0.01895,
                     "length_m": 0.11572, "breadth_m": 0.05117},
    "nut":          {"size": "34.6 x 30 x 17 mm",        "height_m": 0.017,
                     "length_m": 0.0346,  "breadth_m": 0.030},
    "pipe":         {"size": "120 x 110 x 54.5 mm",      "height_m": 0.0545,
                     "length_m": 0.120,   "breadth_m": 0.110,
                     "notes": "Smart grasp via segmentation mask"},
    "sponge":       {"size": "112.58 x 80 x 15.4 mm",    "height_m": 0.01540,
                     "length_m": 0.11258, "breadth_m": 0.080,
                     "notes": "Angled grasp configuration"},
}

def compute_scene_analysis(target: str, detections: list[dict]) -> str:
    lines = []
    target_det = next((d for d in detections if d["object_name"] == target), None)

    # 1. Analyze Elevation (Z-axis)
    lines.append("ELEVATION ANALYSIS:")
    elevation_found = False
    for d in detections:
        known_h = OBJECT_CATALOGUE.get(d["object_name"], {}).get("height_m", None)
        if known_h is None:
            continue
        expected_z = known_h / 2
        excess = d["z"] - expected_z
        if excess <= 0.020:
            continue

        elevation_found = True
        implied_height = excess * 2
        MATCH_TOLERANCE = 0.015
        plausible = []
        for obj_name, obj_info in OBJECT_CATALOGUE.items():
            if obj_name == d["object_name"]:
                continue
            h = obj_info["height_m"]
            if abs(h - implied_height) <= MATCH_TOLERANCE:
                plausible.append(obj_name)

        if d["object_name"] == target:
            lines.append(
                f"  - CAUTION: Target '{target}' has an anomalously high surface (Z={d['z']*1000:.0f}mm). "
                f"Look at the image carefully. If you see a smaller object (like a 'cube') resting on top of OR inside/blocking the {target}, you MUST output 'relocate' for that object."
            )
        else:
            if target in plausible:
                lines.append(
                    f"  - WARNING: {d['object_name']} is elevated (Z={d['z']*1000:.0f}mm) "
                    f"and is likely sitting ON TOP OF target '{target}'. "
                    f"MUST relocate {d['object_name']} first."
                )
            elif plausible:
                lines.append(
                    f"  - INFO: {d['object_name']} is elevated (Z={d['z']*1000:.0f}mm). "
                    f"Likely resting on: {', '.join(plausible)}."
                )
            else:
                lines.append(
                    f"  - Note: {d['object_name']} is elevated but no catalogue "
                    f"object matches the implied support height."
                )

    if not elevation_found:
        lines.append("  - All detected objects are flat on the table.")

    lines.append("")

    # 2. Analyze XY Overlap
    lines.append("OVERLAP ANALYSIS (XY):")
    overlap_found = False

    if target_det:
        target_info = OBJECT_CATALOGUE.get(target, {})
        target_radius = math.hypot(
            target_info.get("length_m", 0.04),
            target_info.get("breadth_m", 0.04),
        ) / 2

        for d in detections:
            if d["object_name"] == target:
                continue
            dist = math.hypot(d["x"] - target_det["x"], d["y"] - target_det["y"])
            d_info = OBJECT_CATALOGUE.get(d["object_name"], {})
            d_radius = math.hypot(
                d_info.get("length_m", 0.04),
                d_info.get("breadth_m", 0.04),
            ) / 2

            print(f"Comparing with '{d['object_name']}':")
            print(f"  dist={dist:.4f}m, target_radius={target_radius:.4f}m, d_radius={d_radius:.4f}m")
            print(f"  sum of radii={target_radius + d_radius:.4f}m, threshold={(target_radius + d_radius) * 0.85:.4f}m")

            if dist < (target_radius + d_radius) * 0.85:
                overlap_found = True
                lines.append(f"  - WARNING: {d['object_name']} overlaps with target {target}.")

    if not target_det:
        lines.append(f"  - Target '{target}' not currently detected by YOLO.")
    elif not overlap_found:
        lines.append(f"  - No objects physically overlap with '{target}'.")

    return "\n".join(lines)

detections = [
    {"object_name": "medicine", "x": 0.3393, "y": -0.2976, "z": 0.0125, "angle_deg": 9.5, "confidence": 0.895},
    {"object_name": "cube", "x": 0.539, "y": -0.0722, "z": 0.0273, "angle_deg": 4.29, "confidence": 0.804},
    {"object_name": "blue marker", "x": 0.4315, "y": -0.1678, "z": 0.017, "angle_deg": -60.52, "confidence": 0.776},
    {"object_name": "black marker", "x": 0.432, "y": -0.0969, "z": 0.0052, "angle_deg": 55.8, "confidence": 0.544},
    {"object_name": "sponge", "x": 0.3041, "y": -0.0788, "z": 0.0121, "angle_deg": 4.83, "confidence": 0.802}
]

print("=== SCENE ANALYSIS RESULTS ===")
print(compute_scene_analysis("black marker", detections))
print("==============================")
