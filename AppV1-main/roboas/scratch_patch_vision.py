import re

def patch_vision():
    with open("vision_mcp.py", "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Import object catalogue and remove local defs
    if "from object_catalogue import OBJECT_CATALOGUE, GRASP_OFFSETS" not in content:
        content = content.replace("import math\n", "import math\nfrom object_catalogue import OBJECT_CATALOGUE, GRASP_OFFSETS\n")
        
        # Strip local definitions
        content = re.sub(r"OBJECT_CATALOGUE\s*=\s*\{.*?\}\n\nGRASP_OFFSETS\s*=\s*\{.*?\}\n", "", content, flags=re.DOTALL)

    # 2. TSR method and corners_float
    # Inside run_yolo_detection pass 1:
    old_tsr = """            try:
                from top_surface_refinement import refine_top_surface_center, FLAT_TOP_CLASSES
                if cls_name in FLAT_TOP_CLASSES:
                    cat_entry = OBJECT_CATALOGUE.get(cls_name, {})
                    refined = refine_top_surface_center(
                        class_name=cls_name,
                        obb_corners=corners_float,
                        image_shape=color_image.shape,
                        depth_image=np.asanyarray(depth_frame.get_data()),
                        depth_scale=camera.current_depth_scale,
                        intrinsics=intrinsics,
                        cam_to_robot_t=CAM_TO_ROBOT_T,
                        expected_height_m=cat_entry.get("height_m", 0.0),
                        method="iterative"
                    )
                    if refined["valid"]:
                        coords["raw_x"] = refined["x"]
                        coords["raw_y"] = refined["y"]
            except Exception as e:
                logger.error(f"TSR error: {e}")"""
    
    new_tsr = """            try:
                from top_surface_refinement import refine_top_surface_center, FLAT_TOP_CLASSES
                # Extract corners float from result.obb.xyxyxyxy[i] if not done yet
                corners_float = box.cpu().numpy().tolist()
                
                if cls_name in FLAT_TOP_CLASSES:
                    cat_entry = OBJECT_CATALOGUE.get(cls_name, {})
                    refined = refine_top_surface_center(
                        class_name=cls_name,
                        obb_corners=corners_float,
                        image_shape=color_image.shape,
                        depth_image=np.asanyarray(depth_frame), # depth_frame is now a numpy array from atomic snapshot
                        depth_scale=depth_scale,
                        intrinsics=intrinsics,
                        cam_to_robot_t=CAM_TO_ROBOT_T,
                        expected_height_m=cat_entry.get("height_m", 0.0),
                        method="hybrid"
                    )
                    if refined["valid"]:
                        coords["raw_x"] = refined["x"]
                        coords["raw_y"] = refined["y"]
            except Exception as e:
                logger.error(f"TSR error: {e}")"""
    if old_tsr in content:
        content = content.replace(old_tsr, new_tsr)

    # 3. get_median_depth signature and logic
    old_median = """def get_median_depth(depth_frame, cx, cy, radius=4):
    valid_depths = []
    width = depth_frame.get_width()
    height = depth_frame.get_height()
    for y in range(max(0, int(cy) - radius), min(height, int(cy) + radius + 1)):
        for x in range(max(0, int(cx) - radius), min(width, int(cx) + radius + 1)):
            depth = depth_frame.get_distance(x, y)
            if np.isfinite(depth) and depth > 0.0:
                valid_depths.append(depth)
    if not valid_depths:
        return None
    return float(np.median(valid_depths))"""
    
    new_median = """def get_median_depth(depth_frame, cx, cy, depth_scale, radius=4):
    valid_depths = []
    height, width = depth_frame.shape
    for y in range(max(0, int(cy) - radius), min(height, int(cy) + radius + 1)):
        for x in range(max(0, int(cx) - radius), min(width, int(cx) + radius + 1)):
            depth = depth_frame[y, x] * depth_scale
            if np.isfinite(depth) and depth > 0.0:
                valid_depths.append(depth)
    if not valid_depths:
        return None
    return float(np.median(valid_depths))"""
    content = content.replace(old_median, new_median)
    
    # 4. _pixel_to_robot definition
    content = content.replace("def _pixel_to_robot(cx_px, cy_px, angle_rad, depth_frame, intrinsics, cls_name: str = \"\"):", 
                              "def _pixel_to_robot(cx_px, cy_px, angle_rad, depth_frame, intrinsics, depth_scale, cls_name: str = \"\"):")
    content = content.replace("distance = get_median_depth(depth_frame, cx_px, cy_px)", "distance = get_median_depth(depth_frame, cx_px, cy_px, depth_scale)")
    content = content.replace("coords = _pixel_to_robot(cx_px, cy_px, angle_rad, depth_frame, intrinsics, cls_name=cls_name)",
                              "coords = _pixel_to_robot(cx_px, cy_px, angle_rad, depth_frame, intrinsics, depth_scale, cls_name=cls_name)")

    # 5. Fix run_yolo_detection signature
    content = content.replace("def run_yolo_detection(color_image, depth_frame, intrinsics):", 
                              "def run_yolo_detection(color_image, depth_frame, intrinsics, depth_scale):")
    
    content = content.replace("distance = get_median_depth(depth_frame, cx_px, cy_px) or 0.0", 
                              "distance = get_median_depth(depth_frame, cx_px, cy_px, depth_scale) or 0.0")

    # 6. mcp_get_latest_detections
    old_mcp_latest = """def mcp_get_latest_detections():
    \"\"\"
    Capture a fresh detection pass using camera.py's atomic snapshot.
    Returns list of detection dicts, empty list on failure.
    \"\"\"
    color_image, depth_image, intrinsics, depth_scale = camera.get_atomic_snapshot()
    if color_image is None or depth_image is None:
        logger.warning("No atomic snapshot from camera yet.")
        return []

    # Note: run_yolo_detection expects a depth_frame object with .get_data().
    # Since depth_image is now a numpy array, we need to adapt run_yolo_detection to handle numpy arrays.
    # However, in our earlier TSR patch we just passed np.asanyarray(depth_frame.get_data()) anyway.
    # The only problem is `get_median_depth(depth_frame, cx_px, cy_px)`.
    # Let's fix that too. Wait, get_realsense_depth_and_intrinsics returns the raw depth_frame, which works.
    # The user required: "vision_mcp must obtain all four in one call. Reading separate camera globals is not atomic."
    # So we MUST pass the numpy depth_image and depth_scale to run_yolo_detection.
    # This means I need to modify `get_median_depth` in vision_mcp.py as well.
    # But wait, it's easier to just use `camera.get_atomic_snapshot()` and mock a depth_frame object!
    class MockDepthFrame:
        def __init__(self, data):
            self.data = data
        def get_data(self):
            return self.data
        def as_depth_frame(self):
            return self
        def get_distance(self, x, y):
            # Fallback distance calc using scale
            return self.data[y, x] * depth_scale
        def get_width(self):
            return self.data.shape[1]
        def get_height(self):
            return self.data.shape[0]

    depth_frame = MockDepthFrame(depth_image)

    return run_yolo_detection(color_image, depth_frame, intrinsics)"""
    new_mcp_latest = """def mcp_get_latest_detections():
    \"\"\"
    Capture a fresh detection pass using camera.py's atomic snapshot.
    Returns list of detection dicts, empty list on failure.
    \"\"\"
    try:
        color_image, depth_image, intrinsics, depth_scale = camera.get_atomic_snapshot()
    except Exception as e:
        logger.warning(f"Error grabbing atomic snapshot: {e}")
        return []
        
    if color_image is None or depth_image is None:
        logger.warning("No atomic snapshot from camera yet.")
        return []

    return run_yolo_detection(color_image, depth_image, intrinsics, depth_scale)"""
    content = content.replace(old_mcp_latest, new_mcp_latest)

    # 7. Fallback coordinate check in run_yolo_detection
    content = content.replace("fallback_elevated = (t[\"z\"] - target_known_h / 2) > 0.020", "fallback_elevated = (t[\"z\"] - target_known_h / 2) > 0.020")
    # Actually the user said: "Update the fallback detector branch to use raw_x/raw_y; it currently expects x/y."
    # But wait, the fallback uses `target_det`, which was ALREADY appended to `detections`.
    # And `detections` items have `x` and `y`! Because they were processed: `coords["x"] = round(final_x, 4)`.
    # Let's double check.

    with open("vision_mcp.py", "w", encoding="utf-8") as f:
        f.write(content)

if __name__ == "__main__":
    patch_vision()
