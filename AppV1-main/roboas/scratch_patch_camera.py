import re

def patch_camera():
    with open("camera.py", "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Add frame_lock
    # Search for latest_color_image
    content = content.replace("latest_color_image = None", "latest_color_image = None\nframe_lock = threading.Lock()")

    # 2. get_atomic_snapshot returns copies
    atomic = """def get_atomic_snapshot():
    with frame_lock:
        if latest_color_image is None or latest_depth_image is None:
            return None, None, None, None
        return (
            latest_color_image.copy(),
            latest_depth_image.copy(),
            latest_depth_intrinsics,
            float(current_depth_scale)
        )"""
    # Replace existing get_camera_snapshot or add get_atomic_snapshot
    if "def get_atomic_snapshot():" in content:
        # already there
        pass
    else:
        # insert before get_camera_snapshot
        content = content.replace("def get_camera_snapshot():", atomic + "\n\ndef get_camera_snapshot():")

    # 3. Heartbeat after person inference
    # Remove the old threading heartbeat daemon if it's there
    content = re.sub(r"def _heartbeat_daemon\(\).*?threading\.Thread\(target=_heartbeat_daemon, daemon=True\)\.start\(\)", "", content, flags=re.DOTALL)
    
    # Inside _vision_loop_inner, after person inference:
    # "Send a heartbeat only after successful safety_model person-detector inference."
    inference_code = """        # Safety pass (Person Detection) using YOLOv8n
        person_detected = False
        with inference_lock:
            try:
                safety_results = safety_model(color_image, verbose=False, classes=[0], conf=0.50)
                if safety_results and len(safety_results) > 0 and len(safety_results[0].boxes) > 0:
                    person_detected = True
            except Exception as e:
                logger.error(f"Safety model error: {e}")"""
                
    new_inference_code = """        # Safety pass (Person Detection) using YOLOv8n
        person_detected = False
        with inference_lock:
            try:
                safety_results = safety_model(color_image, verbose=False, classes=[0], conf=0.50)
                if safety_results and len(safety_results) > 0 and len(safety_results[0].boxes) > 0:
                    person_detected = True
                
                # Inference succeeded. Send heartbeat synchronously.
                try:
                    import os
                    token = os.environ.get("CAMERA_HEARTBEAT_TOKEN", "default-secure-token-xyz")
                    requests.post(HEARTBEAT_URL, json={"token": token}, timeout=0.5)
                except Exception as e:
                    logger.debug(f"Failed to send camera heartbeat: {e}")
            except Exception as e:
                logger.error(f"Safety model error: {e}")"""
    
    if old_inference := re.search(r"# Safety pass \(Person Detection\).*?logger\.error\(f\"Safety model error: \{e\}\"\)", content, re.DOTALL):
        content = content.replace(old_inference.group(0), new_inference_code)

    # Make sure we use frame_lock when updating frames
    update_frames = """        latest_color_image = color_image
        latest_depth_image = depth_image
        latest_depth_intrinsics = depth_intrinsics
        latest_3d_coords = None"""
    new_update_frames = """        with frame_lock:
            latest_color_image = color_image
            latest_depth_image = depth_image
            latest_depth_intrinsics = depth_intrinsics
            latest_3d_coords = None"""
    content = content.replace(update_frames, new_update_frames)

    with open("camera.py", "w", encoding="utf-8") as f:
        f.write(content)

if __name__ == "__main__":
    patch_camera()
