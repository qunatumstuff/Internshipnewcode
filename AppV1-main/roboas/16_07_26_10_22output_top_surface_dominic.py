import base64
import math
import threading
import time

import cv2
import numpy as np
import pyrealsense2 as rs
from mcp.server.fastmcp import FastMCP
from ultralytics import YOLO


# -----------------------------------------------------------------------------
# 1. Global state
# -----------------------------------------------------------------------------
current_rgb_frame = None
current_target_class = None

# This version stores the refined top-surface centre in the ROBOT BASE frame.
latest_3d_coords = {
    "x": 0.0,
    "y": 0.0,
    "z": 0.0,
    "frame": "robot_base",
    "method": "none",
}

mcp = FastMCP("TIEFA_Module_B_Vision")

# Main oriented-bounding-box detector.
model = YOLO("best (12).pt")

# Fallback axis-aligned detector used only if the OBB model returns no OBBs.
obb_discontinuity = YOLO("best (14).pt")

# The segmentation model is deliberately not used for centre calculation.
# Keeping the line commented makes it easy to restore for visualisation only.
# segment = YOLO("best (11).pt")


# -----------------------------------------------------------------------------
# 2. Camera-to-robot calibration
# -----------------------------------------------------------------------------
CAM_TO_ROBOT_T = np.array(
    [
        [0.7389493262, 0.5903177251, -0.3247751171, 0.7326856827],
        [0.6725179732, -0.6755053173, 0.3023444095, -0.4961713772],
        [-0.0409080545, -0.4418343012, -0.8961634791, 0.8225271939],
        [0.0000000000, 0.0000000000, 0.0000000000, 1.0000000000],
], dtype=np.float64)
    


PERMA_OFFSET_X = 0.010823847
PERMA_OFFSET_Y = -0.01782065


# -----------------------------------------------------------------------------
# 3. Top-surface refinement settings
# -----------------------------------------------------------------------------
# Available values:
#   "known_height" -> Method 1: use table Z + known object height.
#   "auto"         -> Method 2: find the highest well-supported Z layer.
#   "hybrid"       -> try known-height first, then fall back to automatic.
TOP_SURFACE_METHOD = "hybrid"

# Set this to False only if robot-base Z becomes SMALLER when an object is higher.
ROBOT_Z_INCREASES_UPWARD = True

# Existing known dimensions. Verify these against the exact physical orientation
# in which each object is placed on the table.
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


# OBB/depth sampling.
OBJECT_MASK_SCALE = 0.92
TABLE_RING_INNER_SCALE = 1.08
TABLE_RING_OUTER_SCALE = 1.45
POINT_SAMPLE_STRIDE = 2
MIN_VALID_DEPTH_M = 0.10
MAX_VALID_DEPTH_M = 2.00

# Surface filtering.
TABLE_CLEARANCE_M = 0.003
KNOWN_HEIGHT_TOLERANCE_M = 0.006
KNOWN_HEIGHT_MIN_POINTS = 15

AUTO_Z_BIN_SIZE_M = 0.002
AUTO_BAND_HALF_WIDTH_M = 0.003
AUTO_MIN_POINTS = 20
AUTO_MIN_FRACTION = 0.08
AUTO_FALLBACK_PERCENTILE = 90.0

# Display selected top-surface depth pixels for debugging.
DRAW_SELECTED_TOP_POINTS = True
MAX_DRAWN_TOP_POINTS = 150


# -----------------------------------------------------------------------------
# 4. MCP tools
# -----------------------------------------------------------------------------
@mcp.tool()
def get_camera_snapshot() -> str:
    """Return the current RGB frame as a Base64-encoded JPEG."""
    global current_rgb_frame

    if current_rgb_frame is None:
        return "Error: Camera frame not ready."

    ok, buffer = cv2.imencode(".jpg", current_rgb_frame)
    if not ok:
        return "Error: Could not encode camera frame."

    base64_str = base64.b64encode(buffer).decode("utf-8")
    return f"data:image/jpeg;base64,{base64_str}"


@mcp.tool()
def set_tracking_target(target_name: str) -> str:
    """Set the object class whose refined coordinates should be published."""
    global current_target_class
    current_target_class = target_name.strip().lower()
    return f"Success: Module B is now tracking '{target_name}' at 30Hz."


# -----------------------------------------------------------------------------
# 5. Geometry and depth helpers
# -----------------------------------------------------------------------------
def rotation_matrix_to_euler_angles(rotation_matrix: np.ndarray) -> np.ndarray:
    sy = math.sqrt(
        rotation_matrix[0, 0] * rotation_matrix[0, 0]
        + rotation_matrix[1, 0] * rotation_matrix[1, 0]
    )
    singular = sy < 1e-6

    if not singular:
        roll = math.atan2(rotation_matrix[2, 1], rotation_matrix[2, 2])
        pitch = math.atan2(-rotation_matrix[2, 0], sy)
        yaw = math.atan2(rotation_matrix[1, 0], rotation_matrix[0, 0])
    else:
        roll = math.atan2(-rotation_matrix[1, 2], rotation_matrix[1, 1])
        pitch = math.atan2(-rotation_matrix[2, 0], sy)
        yaw = 0.0

    return np.array([roll, pitch, yaw], dtype=np.float64)


def triangle_area(x1, y1, x2, y2, x3, y3):
    return abs(
        (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2)) / 2.0
    )


def is_inside_workspace(point_x_mm: float, point_y_mm: float) -> bool:
    """Preserve the original triangular workspace test with tolerance."""
    total = triangle_area(250, -370, 250, 0, 585, 0)
    area_1 = triangle_area(point_x_mm, point_y_mm, 250, 0, 585, 0)
    area_2 = triangle_area(250, -370, point_x_mm, point_y_mm, 585, 0)
    area_3 = triangle_area(250, -370, 250, 0, point_x_mm, point_y_mm)
    return math.isclose(total, area_1 + area_2 + area_3, abs_tol=1e-3)


def scale_corners(corners: np.ndarray, scale: float) -> np.ndarray:
    corners = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    center = corners.mean(axis=0)
    return center + scale * (corners - center)


def polygon_mask(image_shape, corners: np.ndarray) -> np.ndarray:
    mask = np.zeros(image_shape[:2], dtype=np.uint8)
    cv2.fillConvexPoly(mask, np.round(corners).astype(np.int32), 255)
    return mask


def create_object_and_table_masks(image_shape, obb_corners: np.ndarray):
    """
    Create:
      1. an inner OBB mask used to collect object depth points;
      2. a ring outside the OBB used to estimate the local table height.
    """
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
    mask: np.ndarray,
    depth_image: np.ndarray,
    depth_scale: float,
    intrinsics,
    cam_to_robot_t: np.ndarray,
    sample_stride: int = POINT_SAMPLE_STRIDE,
):
    """
    Convert valid aligned-depth pixels inside a mask into robot-frame 3D points.

    Returns:
        robot_points: shape (N, 3)
        pixels_xy:    shape (N, 2), matching each 3D point
    """
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
        np.isfinite(depth_m)
        & (depth_m >= MIN_VALID_DEPTH_M)
        & (depth_m <= MAX_VALID_DEPTH_M)
    )
    xs = xs[valid_depth]
    ys = ys[valid_depth]
    depth_m = depth_m[valid_depth]

    if len(xs) == 0:
        return np.empty((0, 3)), np.empty((0, 2), dtype=np.int32)

    camera_points_h = []
    kept_pixels = []

    for x, y, depth in zip(xs, ys, depth_m):
        camera_point = rs.rs2_deproject_pixel_to_point(
            intrinsics,
            [float(x), float(y)],
            float(depth),
        )
        camera_points_h.append(
            [camera_point[0], camera_point[1], camera_point[2], 1.0]
        )
        kept_pixels.append([int(x), int(y)])

    camera_points_h = np.asarray(camera_points_h, dtype=np.float64)
    robot_points_h = (cam_to_robot_t @ camera_points_h.T).T

    return robot_points_h[:, :3], np.asarray(kept_pixels, dtype=np.int32)


def robust_median(values: np.ndarray, minimum_tolerance: float = 0.003):
    """Median with a MAD-based outlier rejection pass."""
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]

    if len(values) == 0:
        return None

    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    tolerance = max(minimum_tolerance, 3.0 * 1.4826 * mad)

    filtered = values[np.abs(values - median) <= tolerance]
    if len(filtered) == 0:
        return median

    return float(np.median(filtered))


def estimate_table_z(
    table_ring_points: np.ndarray,
    object_points: np.ndarray,
):
    """
    Prefer the local ring around the OBB. If that ring is unavailable, estimate
    the table from the low side of the current object's Z distribution.
    """
    if len(table_ring_points) >= 15:
        table_z = robust_median(table_ring_points[:, 2])
        if table_z is not None:
            return table_z, "local_ring"

    if len(object_points) >= 15:
        if ROBOT_Z_INCREASES_UPWARD:
            table_candidate = np.percentile(object_points[:, 2], 10)
        else:
            table_candidate = np.percentile(object_points[:, 2], 90)
        return float(table_candidate), "object_percentile_fallback"

    return None, "unavailable"


def height_above_table(z_values: np.ndarray, table_z: float) -> np.ndarray:
    if ROBOT_Z_INCREASES_UPWARD:
        return z_values - table_z
    return table_z - z_values


def get_known_object_height(class_name: str):
    """Return a known height using exact or substring class matching."""
    normalized = class_name.strip().lower().replace("_", " ")

    if normalized in OBJECT_HEIGHTS_M:
        return OBJECT_HEIGHTS_M[normalized]

    # Longest keys first prevents generic names from winning too early.
    for key in sorted(OBJECT_HEIGHTS_M, key=len, reverse=True):
        if key in normalized:
            return OBJECT_HEIGHTS_M[key]

    return None


def select_top_points_known_height(
    object_points: np.ndarray,
    table_z: float,
    object_height_m: float,
):
    """Method 1: select points close to table height + known object height."""
    expected_height = object_height_m
    measured_heights = height_above_table(object_points[:, 2], table_z)

    selected_indices = np.where(
        np.abs(measured_heights - expected_height)
        <= KNOWN_HEIGHT_TOLERANCE_M
    )[0]

    if len(selected_indices) < KNOWN_HEIGHT_MIN_POINTS:
        return None

    return selected_indices


def select_top_points_auto(object_points: np.ndarray, table_z: float):
    """
    Method 2: find the highest Z band that contains enough supporting points.

    The search is relative to this object's own measured height distribution,
    so short and tall objects are handled without one fixed absolute top Z.
    """
    measured_heights = height_above_table(object_points[:, 2], table_z)

    above_table_indices = np.where(
        np.isfinite(measured_heights)
        & (measured_heights > TABLE_CLEARANCE_M)
    )[0]

    if len(above_table_indices) < AUTO_MIN_POINTS:
        return None

    heights = measured_heights[above_table_indices]

    # Remove the most extreme high outliers before searching for the top layer.
    high_limit = float(np.percentile(heights, 99.5))
    plausible_local = np.where(heights <= high_limit)[0]
    candidate_indices = above_table_indices[plausible_local]
    candidate_heights = measured_heights[candidate_indices]

    required_support = max(
        AUTO_MIN_POINTS,
        int(math.ceil(len(candidate_indices) * AUTO_MIN_FRACTION)),
    )

    if len(candidate_indices) < required_support:
        return None

    min_height = float(np.min(candidate_heights))
    max_height = float(np.max(candidate_heights))

    if max_height - min_height < 1e-6:
        return candidate_indices

    # Test candidate bands from highest to lowest. A band is considered
    # consistent when it contains enough points within a small vertical range.
    band_centers = np.arange(
        max_height,
        min_height - AUTO_Z_BIN_SIZE_M,
        -AUTO_Z_BIN_SIZE_M,
    )

    for band_center in band_centers:
        local = np.where(
            np.abs(candidate_heights - band_center)
            <= AUTO_BAND_HALF_WIDTH_M
        )[0]

        if len(local) >= required_support:
            return candidate_indices[local]

    # Conservative fallback: form a band around the upper percentile, still
    # requiring multiple supporting points rather than selecting one max point.
    fallback_height = float(
        np.percentile(candidate_heights, AUTO_FALLBACK_PERCENTILE)
    )
    local = np.where(
        np.abs(candidate_heights - fallback_height)
        <= KNOWN_HEIGHT_TOLERANCE_M
    )[0]

    if len(local) >= AUTO_MIN_POINTS:
        return candidate_indices[local]

    return None


def refine_top_surface_center(
    class_name: str,
    obb_corners: np.ndarray,
    image_shape,
    depth_image: np.ndarray,
    depth_scale: float,
    intrinsics,
    method: str,
):
    """
    Refine the existing OBB into a 3D top-surface centre.

    Returns a dictionary containing robot coordinates, display pixel, table Z,
    measured height, selected points and which method actually succeeded.
    """
    object_mask, table_ring_mask, inner_corners = create_object_and_table_masks(
        image_shape,
        obb_corners,
    )

    object_points, object_pixels = mask_to_robot_points(
        object_mask,
        depth_image,
        depth_scale,
        intrinsics,
        CAM_TO_ROBOT_T,
    )
    table_ring_points, _ = mask_to_robot_points(
        table_ring_mask,
        depth_image,
        depth_scale,
        intrinsics,
        CAM_TO_ROBOT_T,
        sample_stride=max(2, POINT_SAMPLE_STRIDE),
    )

    if len(object_points) < AUTO_MIN_POINTS:
        return None

    table_z, table_source = estimate_table_z(table_ring_points, object_points)
    if table_z is None:
        return None

    method = method.lower().strip()
    selected_indices = None
    method_used = None
    known_height = get_known_object_height(class_name)

    if method in {"known_height", "hybrid"} and known_height is not None:
        selected_indices = select_top_points_known_height(
            object_points,
            table_z,
            known_height,
        )
        if selected_indices is not None:
            method_used = "known_height"

    if selected_indices is None and method in {"auto", "hybrid"}:
        selected_indices = select_top_points_auto(object_points, table_z)
        if selected_indices is not None:
            method_used = "auto"

    # If known_height was explicitly selected but the class has no stored height,
    # fail visibly instead of silently returning a wrong result.
    if selected_indices is None:
        return None

    top_points = object_points[selected_indices]
    top_pixels = object_pixels[selected_indices]

    robot_center = np.median(top_points, axis=0)
    pixel_center = np.median(top_pixels, axis=0)
    pixel_center = tuple(np.round(pixel_center).astype(int))

    measured_height = float(
        np.median(height_above_table(top_points[:, 2], table_z))
    )

    return {
        "robot_center": robot_center,
        "pixel_center": pixel_center,
        "top_points": top_points,
        "top_pixels": top_pixels,
        "point_count": int(len(top_points)),
        "table_z": float(table_z),
        "table_source": table_source,
        "measured_height": measured_height,
        "known_height": known_height,
        "method_used": method_used,
        "inner_corners": inner_corners,
    }


def local_median_depth_point(depth_frame, intrinsics, center_x: int, center_y: int):
    """Fallback using a 7x7 depth patch instead of one noisy depth pixel."""
    samples = []
    for y in range(max(0, center_y - 3), min(480, center_y + 4)):
        for x in range(max(0, center_x - 3), min(640, center_x + 4)):
            depth = depth_frame.get_distance(x, y)
            if np.isfinite(depth) and MIN_VALID_DEPTH_M <= depth <= MAX_VALID_DEPTH_M:
                samples.append(depth)

    if not samples:
        return None

    median_depth = float(np.median(samples))
    camera_point = rs.rs2_deproject_pixel_to_point(
        intrinsics,
        [float(center_x), float(center_y)],
        median_depth,
    )
    robot_point = CAM_TO_ROBOT_T @ np.array(
        [camera_point[0], camera_point[1], camera_point[2], 1.0],
        dtype=np.float64,
    )
    return robot_point[:3]


def apply_zoom(image: np.ndarray, zoom: float, focus_xy=None) -> np.ndarray:
    """Zoom after drawing overlays so box and centre stay in the same coordinates."""
    if zoom <= 1.001:
        return image

    height, width = image.shape[:2]
    crop_width = max(1, int(width / zoom))
    crop_height = max(1, int(height / zoom))

    if focus_xy is None:
        focus_x, focus_y = width // 2, height // 2
    else:
        focus_x, focus_y = map(int, focus_xy)

    x1 = int(np.clip(focus_x - crop_width // 2, 0, width - crop_width))
    y1 = int(np.clip(focus_y - crop_height // 2, 0, height - crop_height))

    crop = image[y1 : y1 + crop_height, x1 : x1 + crop_width]
    return cv2.resize(crop, (width, height), interpolation=cv2.INTER_LINEAR)


# -----------------------------------------------------------------------------
# 6. Vision loop
# -----------------------------------------------------------------------------
def vision_loop():
    global current_rgb_frame, latest_3d_coords

    zoom = 1.0
    top_surface_method = TOP_SURFACE_METHOD

    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
    config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)

    profile = pipeline.start(config)
    align = rs.align(rs.stream.color)

    depth_sensor = profile.get_device().first_depth_sensor()
    depth_scale = depth_sensor.get_depth_scale()
    intrinsics = (
        profile.get_stream(rs.stream.color)
        .as_video_stream_profile()
        .get_intrinsics()
    )

    # Preserve the original disparity-shift attempt, but do not stop the program
    # if advanced mode is unavailable on this device/firmware.
    try:
        camera = profile.get_device()
        advanced_mode = rs.rs400_advanced_mode(camera)
        depth_table = advanced_mode.get_depth_table()
        depth_table.disparityShift = 20
        advanced_mode.set_depth_table(depth_table)
    except Exception as exc:
        print(f"[Vision Thread] Advanced-mode warning: {exc}")

    print("[Vision Thread] D435i started. OBB + top-surface depth refinement running.")
    print("[Controls] 1=known height, 2=automatic, 3=hybrid, z/u=zoom, q=quit")

    try:
        while True:
            frames = pipeline.wait_for_frames()
            aligned_frames = align.process(frames)

            depth_frame = aligned_frames.get_depth_frame()
            color_frame = aligned_frames.get_color_frame()

            if not depth_frame or not color_frame:
                continue

            color_image = np.asanyarray(color_frame.get_data())
            depth_image = np.asanyarray(depth_frame.get_data())
            current_rgb_frame = color_image.copy()

            results = model(
                color_image,
                verbose=False,
                agnostic_nms=True,
                iou=0.40,
                conf=0.60,
            )

            fallback_results = obb_discontinuity(
                color_image,
                verbose=False,
                agnostic_nms=True,
                iou=0.40,
                conf=0.60,
            )

            display_focus = None
            found_any_obb = False

            for result in results:
                if result.obb is None or len(result.obb) == 0:
                    continue

                found_any_obb = True

                for obb in result.obb:
                    cls_id = int(obb.cls[0])
                    cls_name = model.names[cls_id]
                    confidence = float(obb.conf[0])

                    corners_float = (
                        obb.xyxyxyxy[0]
                        .cpu()
                        .numpy()
                        .astype(np.float32)
                        .reshape(4, 2)
                    )
                    corners_int = np.round(corners_float).astype(np.int32)

                    obb_center = corners_float.mean(axis=0)
                    obb_center_pixel = tuple(np.round(obb_center).astype(int))

                    angle = float(obb.xywhr[0][4])
                    rotation_from_camera = np.array(
                        [
                            [math.cos(angle), -math.sin(angle), 0.0],
                            [math.sin(angle), math.cos(angle), 0.0],
                            [0.0, 0.0, 1.0],
                        ],
                        dtype=np.float64,
                    )

                    cv2.polylines(
                        color_image,
                        [corners_int],
                        True,
                        (0, 255, 0),
                        2,
                    )
                    cv2.putText(
                        color_image,
                        f"{cls_name} {confidence:.2f}",
                        tuple(corners_int[0] + np.array([0, -10])),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (0, 255, 0),
                        2,
                    )

                    # Yellow dot: original mathematical centre of the OBB.
                    cv2.circle(color_image, obb_center_pixel, 5, (0, 255, 255), -1)

                    refined = refine_top_surface_center(
                        cls_name,
                        corners_float,
                        color_image.shape,
                        depth_image,
                        depth_scale,
                        intrinsics,
                        top_surface_method,
                    )

                    if refined is not None:
                        robot_center = refined["robot_center"]
                        refined_pixel = refined["pixel_center"]
                        display_focus = refined_pixel
                        corrected_x = robot_center[0] - PERMA_OFFSET_X
                        corrected_y = robot_center[1] - PERMA_OFFSET_Y

                        # Magenta dot: refined top-surface centre.
                        cv2.circle(color_image, refined_pixel, 7, (255, 0, 255), -1)

                        # Draw the inner OBB region actually used for depth sampling.
                        cv2.polylines(
                            color_image,
                            [np.round(refined["inner_corners"]).astype(np.int32)],
                            True,
                            (255, 255, 0),
                            1,
                        )

                        if DRAW_SELECTED_TOP_POINTS:
                            top_pixels = refined["top_pixels"]
                            if len(top_pixels) > MAX_DRAWN_TOP_POINTS:
                                draw_indices = np.linspace(
                                    0,
                                    len(top_pixels) - 1,
                                    MAX_DRAWN_TOP_POINTS,
                                    dtype=int,
                                )
                                top_pixels = top_pixels[draw_indices]

                            for px, py in top_pixels:
                                cv2.circle(
                                    color_image,
                                    (int(px), int(py)),
                                    1,
                                    (255, 0, 0),
                                    -1,
                                )

                        rotation_from_matrix = CAM_TO_ROBOT_T[:3, :3]
                        roll, pitch, yaw = rotation_matrix_to_euler_angles(
                            rotation_from_matrix @ rotation_from_camera
                        )

                        is_target = (
                            current_target_class is None
                            or cls_name.lower() == current_target_class.lower()
                        )
                        if is_target:
                            latest_3d_coords.update(
                                {
                                    "x": float(robot_center[0]),
                                    "y": float(robot_center[1]),
                                    "z": float(robot_center[2]),
                                    "frame": "robot_base",
                                    "method": refined["method_used"],
                                    "class": cls_name,
                                }
                            )

                        method_label = refined["method_used"]
                        measured_height_mm = refined["measured_height"] * 1000.0
                        table_z_mm = refined["table_z"] * 1000.0

                        cv2.putText(
                            color_image,
                            (
                                f"TOP {method_label}: X:{corrected_x *1000:.1f} "
                                f"Y:{corrected_y *1000:.1f} "
                                f"Z:{robot_center[2]*1000:.1f}mm"
                            ),
                            (20, 70),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.62,
                            (255, 0, 255),
                            2,
                        )
                        cv2.putText(
                            color_image,
                            (
                                f"height:{measured_height_mm:.1f}mm "
                                f"tableZ:{table_z_mm:.1f}mm "
                                f"points:{refined['point_count']}"
                            ),
                            (20, 96),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.55,
                            (255, 0, 255),
                            2,
                        )

                        

                        print(
                            f"{cls_name} | method={method_label} | "
                            f"X={corrected_x *1000:.2f}mm "
                            f"Y={corrected_y *1000:.2f}mm "
                            f"Z={robot_center[2]*1000:.2f}mm | "
                            f"height={measured_height_mm:.2f}mm | "
                            f"R={math.degrees(roll):.2f} "
                            f"P={math.degrees(pitch):.2f} "
                            f"Y={math.degrees(yaw):.2f} | "
                            f"inside={is_inside_workspace(corrected_x *1000, corrected_y *1000)}"
                        )
                    else:
                        # Depth-refinement failure: keep a safe fallback rather than
                        # crashing or using invalid zero depth.
                        fallback_robot = local_median_depth_point(
                            depth_frame,
                            intrinsics,
                            obb_center_pixel[0],
                            obb_center_pixel[1],
                        )

                        cv2.putText(
                            color_image,
                            f"Top refinement unavailable ({top_surface_method})",
                            (20, 120),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.55,
                            (0, 0, 255),
                            2,
                        )

                        if fallback_robot is not None:
                            cv2.circle(
                                color_image,
                                obb_center_pixel,
                                6,
                                (0, 0, 255),
                                2,
                            )

            # Axis-aligned fallback only when the main model returned no OBBs.
            if not found_any_obb:
                for fallback_result in fallback_results:
                    if fallback_result.boxes is None:
                        continue

                    for box in fallback_result.boxes:
                        cls_id = int(box.cls[0])
                        cls_name = obb_discontinuity.names[cls_id]
                        confidence = float(box.conf[0])
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        center_x = int(round((x1 + x2) / 2.0))
                        center_y = int(round((y1 + y2) / 2.0))

                        fallback_robot = local_median_depth_point(
                            depth_frame,
                            intrinsics,
                            center_x,
                            center_y,
                        )

                        cv2.rectangle(
                            color_image,
                            (x1, y1),
                            (x2, y2),
                            (0, 0, 255),
                            2,
                        )
                        cv2.putText(
                            color_image,
                            f"Fallback: {cls_name} {confidence:.2f}",
                            (x1, max(15, y1 - 10)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.5,
                            (0, 0, 255),
                            2,
                        )

                        if fallback_robot is not None:
                            cv2.circle(
                                color_image,
                                (center_x, center_y),
                                5,
                                (0, 0, 255),
                                -1,
                            )
                            cv2.putText(
                                color_image,
                                (
                                    f"Fallback robot: X:{fallback_robot[0]*1000:.1f} "
                                    f"Y:{fallback_robot[1]*1000:.1f} "
                                    f"Z:{fallback_robot[2]*1000:.1f}mm"
                                ),
                                (20, 145),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.55,
                                (0, 0, 255),
                                2,
                            )

            cv2.putText(
                color_image,
                f"Top mode: {top_surface_method} | 1 known  2 auto  3 hybrid",
                (20, 25),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                (255, 255, 255),
                2,
            )

            display_image = apply_zoom(color_image, zoom, display_focus)
            cv2.imshow("Module B: OBB Top-Surface Centre", display_image)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("1"):
                top_surface_method = "known_height"
                print("[Top Surface] Switched to known-height method")
            elif key == ord("2"):
                top_surface_method = "auto"
                print("[Top Surface] Switched to automatic highest-layer method")
            elif key == ord("3"):
                top_surface_method = "hybrid"
                print("[Top Surface] Switched to hybrid method")
            elif key == ord("z"):
                zoom = min(4.0, zoom + 0.10)
            elif key == ord("u"):
                zoom = max(1.0, zoom - 0.10)

    finally:
        pipeline.stop()
        cv2.destroyAllWindows()


# -----------------------------------------------------------------------------
# 7. Main execution
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    vision_thread = threading.Thread(target=vision_loop, daemon=True)
    vision_thread.start()

    time.sleep(2)
    print("[MCP Server] Starting FastMCP server on main thread...")
    mcp.run()
