"""
XYZ calibration recorder and correction builder.

Purpose
-------
This script calculates a full 3D affine correction matrix (X, Y, and Z) to fix camera
misalignments, using the advanced top-surface tracking method.

Workflow:
    1. Place target object on table.
    2. Press 'c' to lock the Camera's predicted XYZ coordinates for the object.
    3. Manually move the robot arm's TCP perfectly above the object.
    4. Press 'v' to capture the True Robot XYZ (X and Y from robot, Z is hardcoded to 0.03m).
    5. Repeat for several points across the workspace.
    6. Press 'k' to calculate and save the new XYZ correction matrices.
    7. Press 'q' to quit.

"""

import csv
import math
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np
import pyrealsense2 as rs
from ultralytics import YOLO

try:
    from neurapy.robot import Robot
except ImportError:
    print("Warning: neurapy library not found. Robot integration will fail.")

# -----------------------------------------------------------------------------
# 1. User settings
# -----------------------------------------------------------------------------
TARGET_CLASS = "blue cube"
OBB_MODEL_PATH = "best (12).pt"

# Hardcoded true Z height constraint for the calculation
TRUE_Z_M = 0.03  # 30 mm

OUTPUT_DIR = Path("xyz_calibration")
CSV_PATH = OUTPUT_DIR / "xyz_samples.csv"

STREAM_WIDTH = 640
STREAM_HEIGHT = 480
STREAM_FPS = 30

YOLO_CONFIDENCE = 0.60
YOLO_IOU = 0.40

MIN_SAMPLES_FOR_FIT = 5

# Top-surface refinement settings
TOP_SURFACE_METHOD = "hybrid"
ROBOT_Z_INCREASES_UPWARD = True
OBJECT_HEIGHTS_M = {
    "red cube": 0.030,
    "yellow cube": 0.025,
    "blue cube": 0.030,
    "green cube": 0.030,
    "medicine": 0.023,
    "nut": 0.017,
    "black marker": 0.02053,
    "sponge": 0.015,
    "screwdriver": 0.0244,
}

OBJECT_MASK_SCALE = 0.92
TABLE_RING_INNER_SCALE = 1.08
TABLE_RING_OUTER_SCALE = 1.45
POINT_SAMPLE_STRIDE = 2
MIN_VALID_DEPTH_M = 0.10
MAX_VALID_DEPTH_M = 2.00
TABLE_CLEARANCE_M = 0.003
KNOWN_HEIGHT_TOLERANCE_M = 0.006
KNOWN_HEIGHT_MIN_POINTS = 15
AUTO_Z_BIN_SIZE_M = 0.002
AUTO_BAND_HALF_WIDTH_M = 0.003
AUTO_MIN_POINTS = 20
AUTO_MIN_FRACTION = 0.08
AUTO_FALLBACK_PERCENTILE = 90.0

# Current baseline camera-to-robot matrix
CAM_TO_ROBOT_T = np.array(
    [
        [0.7389493262, 0.5903177251, -0.3247751171, 0.7326856827],
        [0.6725179732, -0.6755053173, 0.3023444095, -0.4961713772],
        [-0.0409080545, -0.4418343012, -0.8961634791, 0.8225271939],
        [0.0000000000, 0.0000000000, 0.0000000000, 1.0000000000],
    ], dtype=np.float64)

# -----------------------------------------------------------------------------
# 2. Refinement Helpers
# -----------------------------------------------------------------------------

def triangle_area(x1, y1, x2, y2, x3, y3):
    return abs((x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2)) / 2.0)

def scale_corners(corners: np.ndarray, scale: float) -> np.ndarray:
    corners = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    center = corners.mean(axis=0)
    return center + scale * (corners - center)

def polygon_mask(image_shape, corners: np.ndarray) -> np.ndarray:
    mask = np.zeros(image_shape[:2], dtype=np.uint8)
    cv2.fillConvexPoly(mask, np.round(corners).astype(np.int32), 255)
    return mask

def create_object_and_table_masks(image_shape, obb_corners: np.ndarray):
    object_corners = scale_corners(obb_corners, OBJECT_MASK_SCALE)
    ring_inner_corners = scale_corners(obb_corners, TABLE_RING_INNER_SCALE)
    ring_outer_corners = scale_corners(obb_corners, TABLE_RING_OUTER_SCALE)

    object_mask = polygon_mask(image_shape, object_corners)
    ring_inner_mask = polygon_mask(image_shape, ring_inner_corners)
    ring_outer_mask = polygon_mask(image_shape, ring_outer_corners)
    table_ring_mask = cv2.bitwise_and(
        ring_outer_mask,
        cv2.bitwise_not(ring_inner_mask),
    )
    return object_mask, table_ring_mask, object_corners

def mask_to_robot_points(
    mask: np.ndarray, depth_image: np.ndarray, depth_scale: float,
    intrinsics, cam_to_robot_t: np.ndarray, sample_stride: int = POINT_SAMPLE_STRIDE,
):
    valid = (mask > 0) & (depth_image > 0)
    ys, xs = np.where(valid)

    if len(xs) == 0:
        return np.empty((0, 3)), np.empty((0, 2), dtype=np.int32)

    if sample_stride > 1:
        keep = ((xs % sample_stride) == 0) & ((ys % sample_stride) == 0)
        xs = xs[keep]
        ys = ys[keep]

    raw_depth = depth_image[ys, xs].astype(np.float64)
    depth_m = raw_depth * depth_scale
    valid_depth = (
        np.isfinite(depth_m) & (depth_m >= MIN_VALID_DEPTH_M) & (depth_m <= MAX_VALID_DEPTH_M)
    )
    xs, ys, depth_m = xs[valid_depth], ys[valid_depth], depth_m[valid_depth]

    if len(xs) == 0:
        return np.empty((0, 3)), np.empty((0, 2), dtype=np.int32)

    camera_points_h = []
    kept_pixels = []
    for x, y, depth in zip(xs, ys, depth_m):
        camera_point = rs.rs2_deproject_pixel_to_point(intrinsics, [float(x), float(y)], float(depth))
        camera_points_h.append([camera_point[0], camera_point[1], camera_point[2], 1.0])
        kept_pixels.append([int(x), int(y)])

    camera_points_h = np.asarray(camera_points_h, dtype=np.float64)
    robot_points_h = (cam_to_robot_t @ camera_points_h.T).T
    return robot_points_h[:, :3], np.asarray(kept_pixels, dtype=np.int32)

def robust_median(values: np.ndarray, minimum_tolerance: float = 0.003):
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    if len(values) == 0: return None
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    tolerance = max(minimum_tolerance, 3.0 * 1.4826 * mad)
    filtered = values[np.abs(values - median) <= tolerance]
    return float(np.median(filtered)) if len(filtered) > 0 else median

def estimate_table_z(table_ring_points: np.ndarray, object_points: np.ndarray):
    if len(table_ring_points) >= 15:
        table_z = robust_median(table_ring_points[:, 2])
        if table_z is not None: return table_z, "local_ring"
    if len(object_points) >= 15:
        table_candidate = np.percentile(object_points[:, 2], 10 if ROBOT_Z_INCREASES_UPWARD else 90)
        return float(table_candidate), "object_percentile_fallback"
    return None, "unavailable"

def height_above_table(z_values: np.ndarray, table_z: float) -> np.ndarray:
    return z_values - table_z if ROBOT_Z_INCREASES_UPWARD else table_z - z_values

def get_known_object_height(class_name: str):
    normalized = class_name.strip().lower().replace("_", " ")
    for key in sorted(OBJECT_HEIGHTS_M, key=len, reverse=True):
        if key in normalized: return OBJECT_HEIGHTS_M[key]
    return None

def select_top_points_known_height(object_points: np.ndarray, table_z: float, object_height_m: float):
    measured_heights = height_above_table(object_points[:, 2], table_z)
    selected_indices = np.where(np.abs(measured_heights - object_height_m) <= KNOWN_HEIGHT_TOLERANCE_M)[0]
    return selected_indices if len(selected_indices) >= KNOWN_HEIGHT_MIN_POINTS else None

def select_top_points_auto(object_points: np.ndarray, table_z: float):
    measured_heights = height_above_table(object_points[:, 2], table_z)
    above_table = np.where(np.isfinite(measured_heights) & (measured_heights > TABLE_CLEARANCE_M))[0]
    if len(above_table) < AUTO_MIN_POINTS: return None

    heights = measured_heights[above_table]
    high_limit = float(np.percentile(heights, 99.5))
    candidate_indices = above_table[np.where(heights <= high_limit)[0]]
    candidate_heights = measured_heights[candidate_indices]
    required_support = max(AUTO_MIN_POINTS, int(math.ceil(len(candidate_indices) * AUTO_MIN_FRACTION)))

    if len(candidate_indices) < required_support: return None
    min_height, max_height = float(np.min(candidate_heights)), float(np.max(candidate_heights))
    if max_height - min_height < 1e-6: return candidate_indices

    for band_center in np.arange(max_height, min_height - AUTO_Z_BIN_SIZE_M, -AUTO_Z_BIN_SIZE_M):
        local = np.where(np.abs(candidate_heights - band_center) <= AUTO_BAND_HALF_WIDTH_M)[0]
        if len(local) >= required_support: return candidate_indices[local]

    fallback_height = float(np.percentile(candidate_heights, AUTO_FALLBACK_PERCENTILE))
    local = np.where(np.abs(candidate_heights - fallback_height) <= KNOWN_HEIGHT_TOLERANCE_M)[0]
    return candidate_indices[local] if len(local) >= AUTO_MIN_POINTS else None

def refine_top_surface_center(class_name: str, obb_corners: np.ndarray, image_shape, depth_image: np.ndarray, depth_scale: float, intrinsics, method: str):
    object_mask, table_ring_mask, inner_corners = create_object_and_table_masks(image_shape, obb_corners)
    object_points, object_pixels = mask_to_robot_points(object_mask, depth_image, depth_scale, intrinsics, CAM_TO_ROBOT_T)
    table_ring_points, _ = mask_to_robot_points(table_ring_mask, depth_image, depth_scale, intrinsics, CAM_TO_ROBOT_T, sample_stride=max(2, POINT_SAMPLE_STRIDE))

    if len(object_points) < AUTO_MIN_POINTS: return None
    table_z, table_source = estimate_table_z(table_ring_points, object_points)
    if table_z is None: return None

    method = method.lower().strip()
    selected_indices = None
    known_height = get_known_object_height(class_name)

    if method in {"known_height", "hybrid"} and known_height is not None:
        selected_indices = select_top_points_known_height(object_points, table_z, known_height)
    if selected_indices is None and method in {"auto", "hybrid"}:
        selected_indices = select_top_points_auto(object_points, table_z)

    if selected_indices is None: return None
    top_points = object_points[selected_indices]
    top_pixels = object_pixels[selected_indices]

    robot_center = np.median(top_points, axis=0)
    pixel_center = tuple(np.round(np.median(top_pixels, axis=0)).astype(int))

    return {
        "robot_center": robot_center,
        "pixel_center": pixel_center,
        "inner_corners": inner_corners,
    }


# -----------------------------------------------------------------------------
# 3. CSV and Math Logic
# -----------------------------------------------------------------------------

def ensure_csv_exists():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if CSV_PATH.exists(): return
    with CSV_PATH.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow([
            "sample_id", "timestamp", "class_name", "confidence", "pixel_x", "pixel_y",
            "predicted_x", "predicted_y", "predicted_z", 
            "true_x", "true_y", "true_z", "image_file"
        ])

def next_sample_id():
    if not CSV_PATH.exists(): return 1
    with CSV_PATH.open("r", newline="", encoding="utf-8") as file:
        return max(1, sum(1 for _ in file))

def save_sample(frame, class_name, confidence, pixel_x, pixel_y, predicted_xyz, true_xyz):
    sample_id = next_sample_id()
    image_name = f"xyz_sample_{sample_id:03d}.jpg"
    image_path = OUTPUT_DIR / image_name

    cv2.imwrite(str(image_path), frame)
    with CSV_PATH.open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow([
            sample_id, time.strftime("%Y-%m-%d %H:%M:%S"), class_name, f"{confidence:.6f}",
            pixel_x, pixel_y,
            f"{predicted_xyz[0]:.10f}", f"{predicted_xyz[1]:.10f}", f"{predicted_xyz[2]:.10f}",
            f"{true_xyz[0]:.10f}", f"{true_xyz[1]:.10f}", f"{true_xyz[2]:.10f}", image_name,
        ])
    return sample_id, image_path

def load_xyz_samples():
    predicted_points = []
    true_points = []

    with CSV_PATH.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        for row in reader:
            predicted_points.append([float(row["predicted_x"]), float(row["predicted_y"]), float(row["predicted_z"])])
            true_points.append([float(row["true_x"]), float(row["true_y"]), float(row["true_z"])])

    P = np.asarray(predicted_points, dtype=np.float64)
    T = np.asarray(true_points, dtype=np.float64)

    if len(P) < MIN_SAMPLES_FOR_FIT:
        raise ValueError(f"Need at least {MIN_SAMPLES_FOR_FIT} samples; currently have {len(P)}.")
    
    return P, T

def calculate_and_save_xyz_correction():
    P, T = load_xyz_samples()
    
    # We want a 4x3 matrix M such that P_homog @ M = T
    P_homog = np.column_stack([P, np.ones(len(P))])
    
    # Least squares fit
    M, residuals, rank, s = np.linalg.lstsq(P_homog, T, rcond=None)
    
    # Construct 4x4 Affine Correction Matrix
    # M is 4x3. M.T is 3x4
    XYZ_CORRECTION_T = np.eye(4)
    XYZ_CORRECTION_T[:3, :] = M.T
    
    XYZ_CORRECTED_CAM_TO_ROBOT_T = XYZ_CORRECTION_T @ CAM_TO_ROBOT_T

    # Calculate Errors
    T_pred = (XYZ_CORRECTION_T @ P_homog.T).T[:, :3]
    errors = T_pred - T
    errors_mm = errors * 1000.0
    
    rmse_mm = float(np.sqrt(np.mean(errors_mm ** 2)))
    max_abs_mm = float(np.max(np.abs(errors_mm)))
    
    np.save(OUTPUT_DIR / "XYZ_CORRECTION_T.npy", XYZ_CORRECTION_T)
    np.save(OUTPUT_DIR / "CAM_TO_ROBOT_T_xyz_corrected.npy", XYZ_CORRECTED_CAM_TO_ROBOT_T)
    np.savetxt(OUTPUT_DIR / "XYZ_CORRECTION_T.txt", XYZ_CORRECTION_T, fmt="%.10f")
    np.savetxt(OUTPUT_DIR / "CAM_TO_ROBOT_T_xyz_corrected.txt", XYZ_CORRECTED_CAM_TO_ROBOT_T, fmt="%.10f")

    coefficient_text = (
        f"AFTER_RMSE_MM = {rmse_mm:.4f}\n"
        f"AFTER_MAX_ABS_ERROR_MM = {max_abs_mm:.4f}\n"
        f"\nXYZ_CORRECTION_MATRIX:\n{XYZ_CORRECTION_T}\n"
    )
    (OUTPUT_DIR / "xyz_correction_coefficients.txt").write_text(coefficient_text, encoding="utf-8")

    print("\n[XYZ Correction] Success!")
    print(f"RMSE:  {rmse_mm:.3f} mm")
    print(f"Max absolute error: {max_abs_mm:.3f} mm")
    print("\n[XYZ Correction] XYZ_CORRECTION_T")
    print(XYZ_CORRECTION_T)
    print("\n[XYZ Correction] CAM_TO_ROBOT_T_xyz_corrected")
    print(XYZ_CORRECTED_CAM_TO_ROBOT_T)
    print(f"\n[Saved] {OUTPUT_DIR.resolve()}")

# -----------------------------------------------------------------------------
# 4. Main Recorder loop
# -----------------------------------------------------------------------------
def main():
    ensure_csv_exists()
    
    try:
        robot = Robot()
        print("[Robot] Neura Robot connected via neurapy.")
    except Exception as e:
        robot = None
        print(f"[Robot] Could not connect to robot: {e}. You will not be able to pull true coordinates using 'v'.")

    model = YOLO(OBB_MODEL_PATH)
    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_stream(rs.stream.depth, STREAM_WIDTH, STREAM_HEIGHT, rs.format.z16, STREAM_FPS)
    config.enable_stream(rs.stream.color, STREAM_WIDTH, STREAM_HEIGHT, rs.format.bgr8, STREAM_FPS)

    profile = pipeline.start(config)
    align = rs.align(rs.stream.color)
    intrinsics = profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()

    pending_camera_data = None

    print("[XYZ-Recorder] Started.")
    print(f"[Target] {TARGET_CLASS}")
    print("[Controls] C=Lock Camera Coords, V=Capture Robot True Coords, K=Fit Correction, Q=Quit")
    print(f"[CSV] {CSV_PATH.resolve()}")

    try:
        while True:
            frames = pipeline.wait_for_frames()
            aligned_frames = align.process(frames)
            depth_frame = aligned_frames.get_depth_frame()
            color_frame = aligned_frames.get_color_frame()

            if not depth_frame or not color_frame: continue

            color_image = np.asanyarray(color_frame.get_data())
            depth_image = np.asanyarray(depth_frame.get_data())
            depth_scale = profile.get_device().first_depth_sensor().get_depth_scale()
            
            display_image = color_image.copy()

            # YOLO inference
            results = model(color_image, verbose=False, agnostic_nms=True, iou=YOLO_IOU, conf=YOLO_CONFIDENCE)
            
            best_obb = None
            best_confidence = -1.0
            detected_class = None

            for result in results:
                if result.obb is None: continue
                for obb in result.obb:
                    class_id = int(obb.cls[0])
                    class_name = model.names[class_id].lower()
                    confidence = float(obb.conf[0])
                    if class_name != TARGET_CLASS.lower(): continue
                    if confidence > best_confidence:
                        best_obb = obb
                        best_confidence = confidence
                        detected_class = class_name

            current_camera_xyz = None
            current_pixel = None

            if best_obb is not None:
                box_points = best_obb.xyxyxyxy[0].cpu().numpy().astype(np.float32)
                
                # Apply advanced top-surface refinement
                refined = refine_top_surface_center(
                    class_name=detected_class,
                    obb_corners=box_points,
                    image_shape=color_image.shape,
                    depth_image=depth_image,
                    depth_scale=depth_scale,
                    intrinsics=intrinsics,
                    method=TOP_SURFACE_METHOD,
                )

                if refined is not None:
                    current_camera_xyz = refined["robot_center"]
                    current_pixel = refined["pixel_center"]
                    
                    # Draw UI
                    cv2.polylines(display_image, [np.round(refined["inner_corners"]).astype(np.int32)], True, (255, 255, 0), 1)
                    cv2.circle(display_image, current_pixel, 7, (255, 0, 255), -1)
                    
                    cv2.putText(
                        display_image,
                        f"Target XYZ mm: {current_camera_xyz[0]*1000:.1f}, {current_camera_xyz[1]*1000:.1f}, {current_camera_xyz[2]*1000:.1f}",
                        (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2
                    )

            if pending_camera_data:
                cv2.putText(
                    display_image,
                    "CAMERA LOCKED! Move robot to object, then press 'V'.",
                    (20, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2
                )
                
            cv2.imshow("XYZ Calibration Recorder", display_image)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                break

            # C -> Capture Camera Data
            if key == ord("c"):
                if current_camera_xyz is None:
                    print("[Recorder] No valid target visible to capture.")
                    continue
                
                pending_camera_data = {
                    "frame": display_image.copy(),
                    "class": detected_class,
                    "confidence": best_confidence,
                    "pixel": current_pixel,
                    "predicted_xyz": current_camera_xyz
                }
                print("[Recorder] Camera coordinates locked. Now move the robot TCP above the object and press 'v'.")

            # V -> Capture Robot Data and Save
            if key == ord("v"):
                if not pending_camera_data:
                    print("[Recorder] Press 'c' first to lock camera coordinates.")
                    continue
                
                if not robot:
                    print("[Recorder] Error: Robot not connected. Cannot pull coordinates.")
                    continue
                
                try:
                    # Pull True X, Y from robot TCP
                    robot_pose = robot.get_tcp_pose()
                    true_xyz = np.array([robot_pose[0], robot_pose[1], TRUE_Z_M])
                    
                    sample_id, img_path = save_sample(
                        frame=pending_camera_data["frame"],
                        class_name=pending_camera_data["class"],
                        confidence=pending_camera_data["confidence"],
                        pixel_x=pending_camera_data["pixel"][0],
                        pixel_y=pending_camera_data["pixel"][1],
                        predicted_xyz=pending_camera_data["predicted_xyz"],
                        true_xyz=true_xyz
                    )
                    
                    print(f"[Saved {sample_id}] Camera={pending_camera_data['predicted_xyz']} | True={true_xyz}")
                    pending_camera_data = None # Clear pending
                except Exception as e:
                    print(f"[Recorder] Failed to read robot pose or save: {e}")

            # K -> Calculate Full Matrix
            if key == ord("k"):
                try:
                    calculate_and_save_xyz_correction()
                except Exception as exc:
                    print(f"[XYZ Correction] Failed: {exc}")

    finally:
        pipeline.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
