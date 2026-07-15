import math
import os
import cv2
import numpy as np
import pyrealsense2 as rs
from ultralytics import YOLO
from mcp.server.fastmcp import FastMCP
import threading
import base64
import time
# Unused leftover imports (inference, vision) removed

# -----------------------------------------------------------------------------
# 1. Global State Variables (Shared between Vision Thread and MCP Server)
# -----------------------------------------------------------------------------
current_rgb_frame = None
current_target_class = None  # e.g., "cup", "bottle", "apple"
latest_3d_coords = {"x": 0.0, "y": 0.0, "z": 0.0}
current_depth_frame = None
camera_intrinsics = None
inference_lock = threading.Lock()

# Tweak this value to add a global Z offset (in meters) to all detected objects.
# E.g., setting it to 0.02 will raise the target pick point by 2 cm.
Z_OFFSET = 0
DISPLAY_ONLY_TARGET = True


def smooth_coord(old_val, new_val, alpha=0.2, snap_thresh=0.05):    
    """
    Applies Exponential Moving Average to stabilize flickering coordinates. 
    If the new value jumps significantly (> snap_thresh in meters), it snaps instantly.
    """
    if old_val == 0.0 or abs(old_val - new_val) > snap_thresh:
        return new_val
    return alpha * new_val + (1 - alpha) * old_val

#Formatting
# #Raw Sensor Data
#↓
#Filtering
#↓
#Stable Robot Commands

# Initialize F                                                                                                                                                          1`astMCP Server
mcp = FastMCP("TIEFA_Module_B_Vision")

model=YOLO("best (12).pt")
segment=YOLO("best (11).pt")
obb_discontinuity=YOLO("best (14).pt")

# -----------------------------------------------------------------------------
# 2. MCP Tools Definition (Exposed to System 2 / ZBook)
# -----------------------------------------------------------------------------

tracked=False

@mcp.tool()
def get_camera_snapshot() -> str:
    global current_rgb_frame

    print("GET CAMERA SNAPSHOT FUNCTION CALLED")

    if current_rgb_frame is None:
        print("ERROR: current_rgb_frame is None")
        return "Error: Camera frame not ready."

    zoom = 1.0

    frame = current_rgb_frame.copy()

    height, width, _ = frame.shape
    print(f"Camera frame shape: {width}x{height}")

    new_width = int(width / zoom)
    new_height = int(height / zoom)

    miny = max(0, int((height - new_height) / 2))
    maxy = min(height, miny + new_height)
    minx = max(0, int((width - new_width) / 2))
    maxx = min(width, minx + new_width)

    cropped = frame[miny:maxy, minx:maxx]
    snapshot_frame = cv2.resize(cropped, (width, height))



    print("Took snapshot successfully")

    return f"data:image/jpeg;base64,{snapshot_frame}"

@mcp.tool()
def set_tracking_target(target_name: str) -> str:
    """
    Set the object class for System 1 (YOLO) to track.
    System 2 calls this after reasoning. Example target_name: "bottle"
    """
    global current_target_class
    current_target_class = target_name
    print(current_target_class)

    return current_target_class

# -----------------------------------------------------------------------------
# 3. Vision Loop (Runs in a separate background thread)
# -----------------------------------------------------------------------------

def rotationMatrixToEulerAngles(R):
    sy = math.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2)
    singular = sy < 1e-6

    if not singular:
        x = math.atan2(R[2, 1], R[2, 2])
        y = math.atan2(-R[2, 0], sy)
        z = math.atan2(R[1, 0], R[0, 0])
    else:
        x = math.atan2(-R[1, 2], R[1, 1])
        y = math.atan2(-R[2, 0], sy)
        z = 0

    return np.array([x, y, z])

def get_median_depth(depth_frame, cx, cy, radius=4):
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
    return float(np.median(valid_depths))



def _vision_loop_inner():
    global current_rgb_frame, current_target_class, latest_3d_coords, last_click, spatial_coords, current_depth_frame, camera_intrinsics
    # Configure Intel RealSense pipeline
    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
    config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)

    # Start streaming
    profile = pipeline.start(config)

    # Align depth stream to color stream (Crucial for 3D mapping)
    align_to = rs.stream.color
    align = rs.align(align_to)

    # Get camera intrinsics (Needed for pixel to 3D conversion)
    depth_sensor = profile.get_device().first_depth_sensor()
    camera=profile.get_device()
    advanced_mode = rs.rs400_advanced_mode(camera)
    depth_table=advanced_mode.get_depth_table()
    depth_table.disparityShift=20
    depth_scale = depth_sensor.get_depth_scale()
    intrinsics = profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
    camera_intrinsics = intrinsics

    print("[Vision Thread] D435i Camera Started. YOLO Inference Running...")

    clicked=""

    frame_counter = 0
    results = []
    sponge_detection = []

    try:
        while True:
            frames = pipeline.wait_for_frames()
            aligned_frames = align.process(frames)
            
            depth_frame = aligned_frames.get_depth_frame()
            color_frame = aligned_frames.get_color_frame()
            
            if not depth_frame or not color_frame:
                continue

            # Convert images to numpy arrays
            color_image = np.asanyarray(color_frame.get_data())
            current_rgb_frame = color_image.copy() # Update global state for MCP snapshot
            current_depth_frame = depth_frame

            # Run inference every 4th frame to conserve GPU/VRAM resources for Qwen/Ollama
            if frame_counter % 4 == 0:
                with inference_lock:
                    results = model(color_image, verbose=False, agnostic_nms=True, iou=0.35, conf=0.35)
                    segmentation_results = segment(color_image, verbose=False, agnostic_nms=True, iou=0.35, conf=0.35)

            current_boxes = []

            # Draw segmentation masks for sponge/pipe
            for sponge in segmentation_results:
                if sponge.masks is None:
                    continue

                masks = sponge.masks.data.cpu().numpy()
                class_ids = sponge.boxes.cls.cpu().numpy().astype(int)
                confidences = sponge.boxes.conf.cpu().numpy()

                for mask, class_id, conf in zip(masks, class_ids, confidences):
                    cls_name = segment.names[class_id].lower()

                    is_target = (
                        current_target_class is not None and
                        cls_name == current_target_class.lower()
                    )

                    if DISPLAY_ONLY_TARGET and current_target_class is not None and not is_target:
                        continue

                    mask_binary = cv2.resize(mask, (color_image.shape[1], color_image.shape[0]))
                    mask_binary = ((mask_binary > 0.5) * 255).astype(np.uint8)

                    colored_mask = color_image.copy()
                    colored_mask[mask_binary > 0] = [215, 215, 218]
                    color_image = cv2.addWeighted(color_image, 0.7, colored_mask, 0.3, 0)

                    contours, _ = cv2.findContours(mask_binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    cv2.drawContours(color_image, contours, -1, (0, 255, 0), 2)

                    if len(contours) > 0:
                        largest_contour = max(contours, key=cv2.contourArea)
                        moment = cv2.moments(largest_contour)
                        if moment["m00"] != 0:
                            seg_cx = int(moment["m10"] / moment["m00"])
                            seg_cy = int(moment["m01"] / moment["m00"])
                        else:
                            x, y, w, h = cv2.boundingRect(largest_contour)
                            seg_cx, seg_cy = x + w // 2, y + h // 2

                        # Red centre dot from moments
                        cv2.circle(color_image, (seg_cx, seg_cy), 5, (0, 0, 255), -1)

                        x, y, w, h = cv2.boundingRect(largest_contour)
                        cv2.putText(
                            color_image,
                            f"{cls_name} SEG {conf:.2f}",
                            (x, y - 10),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.5,
                            (0, 255, 0),
                            2
                        )

            # Draw OBB boxes for all detected objects
            for result in results:
                if result.obb is None or len(result.obb) == 0:
                    # ---- FALLBACK: primary OBB model (best (12).pt) found ----
                    # ---- nothing this frame. Try obb_discontinuity          ----
                    # ---- (best (14).pt) as a fallback detector instead.    ----
                    fallback_results = obb_discontinuity(
                        color_image, verbose=False, agnostic_nms=False, iou=0.35, conf=0.35
                    )

                    for fb in fallback_results:
                        if fb.boxes is None:
                            continue

                        for box in fb.boxes:
                            cls_id = int(box.cls[0])
                            cls_name = obb_discontinuity.names[cls_id].lower()
                            confidence = float(box.conf[0])

                            is_target = (
                                current_target_class is not None and
                                cls_name == current_target_class.lower()
                            )

                            if DISPLAY_ONLY_TARGET and current_target_class is not None and not is_target:
                                continue

                            x1, y1, x2, y2 = map(int, box.xyxy[0])
                            center_x = int((x1 + x2) / 2)
                            center_y = int((y1 + y2) / 2)

                            cv2.rectangle(color_image, (x1, y1), (x2, y2), (0, 0, 255), 2)
                            # Red centre dot
                            cv2.circle(color_image, (center_x, center_y), 5, (0, 0, 255), -1)
                            cv2.putText(
                                color_image,
                                f"{cls_name} FALLBACK {confidence:.2f}",
                                (x1, y1 - 10),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.5,
                                (0, 0, 255),
                                2
                            )

                            # Only update latest_3d_coords for the selected target
                            if is_target:
                                CAM_TO_ROBOT_T = np.array([
                                    [0.7328061018, 0.6121545059, -0.2970893437, 0.7217746900],
                                    [0.6799624012, -0.6424940804, 0.3533447178, -0.4958178639],
                                    [-0.0349166256, -0.4652557478, -0.8865538354, 0.8232286668],
                                    [0.0000000000, 0.0000000000, 0.0000000000, 1.0000000000],
                                    ], dtype=np.float64)

                                distance = get_median_depth(depth_frame, center_x, center_y, radius=4)
                                if distance is not None and distance > 0:
                                    spatial_coords = rs.rs2_deproject_pixel_to_point(intrinsics, [center_x, center_y], distance)

                                    
                                    robot = CAM_TO_ROBOT_T @ np.array([spatial_coords[0], spatial_coords[1], spatial_coords[2], 1.0])
                                    latest_3d_coords["x"] = smooth_coord(latest_3d_coords["x"],robot[0])
                                    
                                    latest_3d_coords["y"] = smooth_coord(latest_3d_coords["y"],robot[1])
                                    
                                    latest_3d_coords["z"] = smooth_coord(latest_3d_coords["z"],robot[2])

                                    cv2.putText(
                                        color_image,
                                        f"TARGET {cls_name} (fallback): "
                                        f"X:{robot[0]*1000:.1f} Y:{robot[1]*1000:.1f} Z:{robot[2]*1000:.1f}mm",
                                        (20, 70),
                                        cv2.FONT_HERSHEY_SIMPLEX,
                                        0.7,
                                        (0, 0, 255),
                                        2
                                    )

                    # Done handling this (empty primary) result -- move to next frame's result
                    continue

                for obb in result.obb:
                    cls_id = int(obb.cls[0])
                    cls_name = model.names[cls_id].lower()
                    confidence = float(obb.conf[0])

                    (x1, y1), (x2, y2), (x3, y3), (x4, y4) = obb.xyxyxyxy[0].cpu().numpy().astype(int)

                    current_boxes.append((x1, y1, x2, y2, x3, y3, x4, y4, cls_name, confidence))

                    is_target = (
                        current_target_class is not None and
                        cls_name == current_target_class.lower()
                    )

                    if DISPLAY_ONLY_TARGET and current_target_class is not None and not is_target:
                        continue

                    cv2.polylines(
                        color_image,
                        [np.array([(x1, y1), (x2, y2), (x3, y3), (x4, y4)], dtype=np.int32)],
                        True,
                        (0, 255, 255),
                        2
                    )

                    # Red centre dot at OBB centre
                    obb_cx = int(obb.xywhr[0][0])
                    obb_cy = int(obb.xywhr[0][1])
                    cv2.circle(color_image, (obb_cx, obb_cy), 5, (0, 0, 255), -1)

                    cv2.putText(
                        color_image,
                        f"{cls_name} OBB {confidence:.2f}",
                        (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (0, 255, 255),
                        2
                    )

                    # Only update latest_3d_coords for the selected target
                    if current_target_class and cls_name == current_target_class.lower():
                        CAM_TO_ROBOT_T = np.array([
                            [0.7328061018, 0.6121545059, -0.2970893437, 0.7217746900],
                            [0.6799624012, -0.6424940804, 0.3533447178, -0.4958178639],
                            [-0.0349166256, -0.4652557478, -0.8865538354, 0.8232286668],
                            [0.0000000000, 0.0000000000, 0.0000000000, 1.0000000000],
                            ], dtype=np.float64)

                        rotation_from_matrix = CAM_TO_ROBOT_T[:3, :3]

                        center_x = int(obb.xywhr[0][0])
                        center_y = int(obb.xywhr[0][1])
                        angle = float(obb.xywhr[0][4])

                        rotation_from_camera = np.array([
                            [math.cos(angle), -math.sin(angle), 0],
                            [math.sin(angle),  math.cos(angle), 0],
                            [0,                0,               1]
                        ])

                        distance = get_median_depth(depth_frame, center_x, center_y, radius=4)
                        if distance is None or distance <= 0:
                            continue
                        spatial_coords = rs.rs2_deproject_pixel_to_point(intrinsics, [center_x, center_y], distance)

                        latest_3d_coords["x"] = smooth_coord(latest_3d_coords["x"], spatial_coords[0])
                        latest_3d_coords["y"] = smooth_coord(latest_3d_coords["y"], spatial_coords[1])
                        latest_3d_coords["z"] = smooth_coord(latest_3d_coords["z"], spatial_coords[2] + Z_OFFSET)

                        roll, pitch, yaw = rotationMatrixToEulerAngles(rotation_from_matrix @ rotation_from_camera)
                        robot = CAM_TO_ROBOT_T @ np.array([spatial_coords[0], spatial_coords[1], spatial_coords[2], 1.0])

                        cv2.putText(
                            color_image,
                            f"TARGET {cls_name}: X:{robot[0]*1000:.1f} Y:{robot[1]*1000:.1f} Z:{robot[2]*1000:.1f}mm",
                            (20, 70),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.7,
                            (0, 0, 255),
                            2
                        )
                        

            # Show the live feed (for debugging on i5 laptop)
            cv2.imshow("Module B: System 1 Vision Reflex", color_image)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

            frame_counter += 1
            time.sleep(0.01)

    finally:
        try:
            pipeline.stop()
            cv2.destroyAllWindows()
        except:
            pass

def vision_loop():
    try:
        _vision_loop_inner()
    except Exception as e:
        import traceback
        print(f"[FATAL] Camera Vision Loop crashed: {e}")
        traceback.print_exc()

# -----------------------------------------------------------------------------
# 4. Main Execution
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Start the vision processing in a background thread
    
    vision_thread = threading.Thread(target=vision_loop, daemon=True)
    vision_thread.start()

    # Give the camera 2 seconds to warm up
    time.sleep(2)
    print("[MCP Server] Starting FastMCP Server on Main Thread...")
    
    # Run the MCP server (This blocks the main thread, handling API requests)
    mcp.run()