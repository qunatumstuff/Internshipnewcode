import re

def fix_fallback_vision():
    with open("vision_mcp.py", "r", encoding="utf-8") as f:
        content = f.read()

    # The fallback detector is here:
    old_fallback = """                    coords = _pixel_to_robot(cx_px, cy_px, angle_rad, depth_frame, intrinsics, depth_scale, cls_name=cls_name)
                    if coords is None:
                        continue
                        
                    if not isinside(coords["x"] * 1000, coords["y"] * 1000):
                        continue

                    distance = get_median_depth(depth_frame, cx_px, cy_px, depth_scale) or 0.0
                    w_m = (w_px * distance) / intrinsics.fx if hasattr(intrinsics, 'fx') and intrinsics.fx > 0 else 0
                    h_m = (h_px * distance) / intrinsics.fy if hasattr(intrinsics, 'fy') and intrinsics.fy > 0 else 0
                    detections.append({
                        "source":      "FALLBACK",
                        "object_name": cls_name,
                        "x":           coords["x"],
                        "y":           coords["y"],
                        "z":           coords["z"],
                        "angle_deg":   coords["angle_deg"],
                        "confidence":  round(conf, 3),
                        "cx_px":       cx_px,
                        "cy_px":       cy_px,
                        "w_px":        w_px,
                        "h_px":        h_px,
                        "w_m":         w_m,
                        "h_m":         h_m,
                    })"""
                    
    new_fallback = """                    coords = _pixel_to_robot(cx_px, cy_px, angle_rad, depth_frame, intrinsics, depth_scale, cls_name=cls_name)
                    if coords is None:
                        continue
                    
                    # No TSR for fallback branch (it uses minAreaRect on non-flat objects typically)
                    offsets = GRASP_OFFSETS.get(cls_name.lower(), {"x": 0.0, "y": 0.0, "z": 0.0})
                    final_x = coords["raw_x"] + offsets["x"] - PERMA_OFFSET_X
                    final_y = coords["raw_y"] + offsets["y"] - PERMA_OFFSET_Y
                    final_z = coords["z"] + offsets["z"]

                    coords["x"] = round(final_x, 4)
                    coords["y"] = round(final_y, 4)
                    coords["z"] = round(final_z, 4)
                        
                    if not isinside(coords["x"] * 1000, coords["y"] * 1000):
                        continue

                    distance = get_median_depth(depth_frame, cx_px, cy_px, depth_scale) or 0.0
                    w_m = (w_px * distance) / intrinsics.fx if hasattr(intrinsics, 'fx') and intrinsics.fx > 0 else 0
                    h_m = (h_px * distance) / intrinsics.fy if hasattr(intrinsics, 'fy') and intrinsics.fy > 0 else 0
                    detections.append({
                        "source":      "FALLBACK",
                        "object_name": cls_name,
                        "x":           coords["x"],
                        "y":           coords["y"],
                        "z":           coords["z"],
                        "angle_deg":   coords["angle_deg"],
                        "confidence":  round(conf, 3),
                        "cx_px":       cx_px,
                        "cy_px":       cy_px,
                        "w_px":        w_px,
                        "h_px":        h_px,
                        "w_m":         w_m,
                        "h_m":         h_m,
                    })"""

    content = content.replace(old_fallback, new_fallback)

    with open("vision_mcp.py", "w", encoding="utf-8") as f:
        f.write(content)

if __name__ == "__main__":
    fix_fallback_vision()
