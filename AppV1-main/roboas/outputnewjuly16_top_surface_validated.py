import base64
import math
import threading
import time
from typing import Any, Dict, Optional, Tuple

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
state_lock = threading.Lock()

# The published coordinates represent the detected OBJECT TOP-SURFACE CENTRE.
# They are not yet a robot TCP/flange destination. The robot controller must
# still apply its pre-grasp height, TCP/fingertip length, and grasp clearance.
latest_3d_coords: Dict[str, Any] = {
    "valid": False,
    "timestamp": 0.0,
    "x": None,
    "y": None,
    "z": None,
    "yaw_rad": None,
    "frame": "robot_base",
    "coordinate_type": "object_top_surface_center_not_tcp",
    "method": "none",
    "class": None,
    "confidence": 0.0,
    "reason": "camera_not_ready",
}

mcp = FastMCP("TIEFA_Module_B_Vision")

# Main oriented-bounding-box detector.
model = YOLO("best (12).pt")

# Axis-aligned fallback detector. It is now run only when no OBB is available.
obb_discontinuity = YOLO("best (14).pt")


# -----------------------------------------------------------------------------
# 2. Camera-to-robot calibration
# -----------------------------------------------------------------------------
CAM_TO_ROBOT_T = np.array(
    [
        [0.7389493262, 0.5903177251, -0.3247751171, 0.7326856827],
        [0.6725179732, -0.6755053173, 0.3023444095, -0.4961713772],
        [-0.0272403049, -0.4667249144, -0.8845155980, 0.8220003503],
        [0.0, 0.0, 0.0, 1.0],
    ],
    dtype=np.float64,
)


# -----------------------------------------------------------------------------
# 3. Top-surface refinement settings
# -----------------------------------------------------------------------------
# Modes:
#   "known_height" -> select a plane near table Z + catalogue height.
#   "auto"         -> find the highest well-supported, flat surface.
#   "hybrid"       -> automatic first, validate against catalogue height,
#                     then use known-height only if automatic detection fails.
TOP_SURFACE_METHOD = "hybrid"

# Set False only if a physically higher object produces a smaller robot-base Z.
ROBOT_Z_INCREASES_UPWARD = True

# Coordinates older than this are not returned as usable by the MCP tool.
MAX_COORDINATE_AGE_S = 0.50

# Robot workspace. These are the rectangular bounds previously defined for the
# camera workspace, expressed in robot-base metres.
WORKSPACE_X_MIN_M = 0.250
WORKSPACE_X_MAX_M = 0.585
WORKSPACE_Y_MIN_M = -0.370
WORKSPACE_Y_MAX_M = 0.000

# Top-plane refinement is only safe for classes whose intended grasp surface is
# approximately flat. Add/remove keywords to match the exact YOLO class names.
FLAT_TOP_CLASS_KEYWORDS = {
    "cube",
    "medicine",
    "medicine box",
    "box",
}

# Known physical object heights in the orientation used on the table.
OBJECT_HEIGHTS_M = {
    "red cube": 0.040,
    "yellow cube": 0.040,
    "cube": 0.040,
    "medicine": 0.01895,
    "medicine box": 0.01895,
    "nut": 0.017,
    "black marker": 0.02053,
    "blue marker": 0.02053,
    "green marker": 0.02053,
    "marker": 0.02053,
    "pipe": 0.0545,
}

# Reject an automatically measured surface when its height differs too much
# from the catalogue height. A disagreement is rejected rather than merged.
MAX_HEIGHT_ERROR_M = 0.010
VALIDATE_AUTO_WITH_KNOWN_HEIGHT = True

# OBB/depth sampling.
OBJECT_MASK_SCALE = 0.92
TABLE_RING_INNER_SCALE = 1.08
TABLE_RING_OUTER_SCALE = 1.45
POINT_SAMPLE_STRIDE = 2
MIN_VALID_DEPTH_M = 0.10
MAX_VALID_DEPTH_M = 2.00
MIN_OBJECT_POINTS = 20
MIN_TABLE_RING_POINTS = 15

# Height selection.
TABLE_CLEARANCE_M = 0.003
KNOWN_HEIGHT_TOLERANCE_M = 0.006
KNOWN_HEIGHT_MIN_POINTS = 15
AUTO_Z_BIN_SIZE_M = 0.002
AUTO_BAND_HALF_WIDTH_M = 0.003
AUTO_MIN_POINTS = 20
AUTO_MIN_FRACTION = 0.08
AUTO_HIGH_OUTLIER_PERCENTILE = 99.5
AUTO_FALLBACK_PERCENTILE = 90.0

# Flat-surface validation. These are starting values and should be tuned using
# physical D435i tests at the real working distance.
SURFACE_PLANE_DISTANCE_M = 0.004
SURFACE_MIN_INLIER_FRACTION = 0.60
SURFACE_MAX_TILT_DEG = 20.0
SURFACE_MAX_P90_RESIDUAL_M = 0.0045
SURFACE_MIN_XY_SPAN_M = 0.006
SURFACE_MIN_XY_AREA_M2 = 0.00005  # 50 mm^2

# Display selected top-surface pixels for debugging.
DRAW_SELECTED_TOP_POINTS = True
MAX_DRAWN_TOP_POINTS = 150


# -----------------------------------------------------------------------------
# 4. State and MCP helpers
# -----------------------------------------------------------------------------
def normalize_name(name: Optional[str]) -> str:
    if not name:
        return ""
    return " ".join(name.strip().lower().replace("_", " ").split())


def invalidate_latest_coordinates(reason: str) -> None:
    """Invalidate old coordinates so the robot cannot consume stale data."""
    with state_lock:
        latest_3d_coords.update(
            {
                "valid": False,
                "timestamp": time.time(),
                "x": None,
                "y": None,
                "z": None,
                "yaw_rad": None,
                "method": "none",
                "class": None,
                "confidence": 0.0,
                "reason": reason,
            }
        )


def publish_latest_coordinates(candidate: Dict[str, Any]) -> None:
    now = time.time()
    with state_lock:
        latest_3d_coords.update(
            {
                "valid": True,
                "timestamp": now,
                "x": float(candidate["robot_center"][0]),
                "y": float(candidate["robot_center"][1]),
                "z": float(candidate["robot_center"][2]),
                "yaw_rad": float(candidate["yaw_rad"]),
                "frame": "robot_base",
                "coordinate_type": "object_top_surface_center_not_tcp",
                "method": candidate["method_used"],
                "class": candidate["class_name"],
                "confidence": float(candidate["confidence"]),
                "reason": "ok",
                "measured_height_m": float(candidate["measured_height"]),
                "expected_height_m": (
                    None
                    if candidate["known_height"] is None
                    else float(candidate["known_height"])
                ),
                "height_error_m": (
                    None
                    if candidate["height_error"] is None
                    else float(candidate["height_error"])
                ),
                "height_validated": bool(candidate["height_validated"]),
                "point_count": int(candidate["point_count"]),
                "plane_tilt_deg": float(candidate["plane_tilt_deg"]),
                "plane_p90_residual_m": float(
                    candidate["plane_p90_residual_m"]
                ),
                "surface_area_m2": float(candidate["surface_area_m2"]),
                "workspace_valid": True,
            }
        )


@mcp.tool()
def get_camera_snapshot() -> str:
    """Return the current RGB frame as a Base64-encoded JPEG."""
    with state_lock:
        if current_rgb_frame is None:
            return "Error: Camera frame not ready."
        frame_copy = current_rgb_frame.copy()

    ok, buffer = cv2.imencode(".jpg", frame_copy)
    if not ok:
        return "Error: Could not encode camera frame."

    base64_str = base64.b64encode(buffer).decode("utf-8")
    return f"data:image/jpeg;base64,{base64_str}"


@mcp.tool()
def set_tracking_target(target_name: str) -> str:
    """Set the object class whose validated coordinates should be published."""
    global current_target_class

    normalized = normalize_name(target_name)
    with state_lock:
        current_target_class = normalized or None

    invalidate_latest_coordinates("tracking_target_changed")
    return f"Success: Module B is now tracking '{target_name}'."


@mcp.tool()
def get_target_coordinates() -> Dict[str, Any]:
    """
    Return the latest validated top-surface centre in robot-base coordinates.

    The returned point is not a TCP/flange pose. The robot controller must still
    apply gripper/TCP geometry and pre-grasp/grasp clearances.
    """
    with state_lock:
        result = dict(latest_3d_coords)

    age_s = max(0.0, time.time() - float(result.get("timestamp", 0.0)))
    result["age_s"] = age_s

    if result.get("valid") and age_s > MAX_COORDINATE_AGE_S:
        result["valid"] = False
        result["reason"] = "stale_coordinates"

    return result


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


def is_inside_workspace(robot_x_m: float, robot_y_m: float) -> bool:
    """Return True only inside the configured rectangular robot workspace."""
    return (
        WORKSPACE_X_MIN_M <= robot_x_m <= WORKSPACE_X_MAX_M
        and WORKSPACE_Y_MIN_M <= robot_y_m <= WORKSPACE_Y_MAX_M
    )


def class_matches_target(class_name: str, target_name: Optional[str]) -> bool:
    if not target_name:
        return True
    return normalize_name(class_name) == normalize_name(target_name)


def is_flat_top_class(class_name: str) -> bool:
    normalized = normalize_name(class_name)
    return any(keyword in normalized for keyword in FLAT_TOP_CLASS_KEYWORDS)


def scale_corners(corners: np.ndarray, scale: float) -> np.ndarray:
    corners = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    center = corners.mean(axis=0)
    return center + scale * (corners - center)


def polygon_mask(image_shape, corners: np.ndarray) -> np.ndarray:
    mask = np.zeros(image_shape[:2], dtype=np.uint8)
    cv2.fillConvexPoly(mask, np.round(corners).astype(np.int32), 255)
    return mask


def create_object_and_table_masks(image_shape, obb_corners: np.ndarray):
    """Create an inner OBB object mask and an exterior local-table ring."""
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
    """Convert valid aligned-depth pixels in a mask to robot-frame points."""
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

    camera_points_h = np.empty((len(xs), 4), dtype=np.float64)
    camera_points_h[:, 3] = 1.0

    for index, (x, y, depth) in enumerate(zip(xs, ys, depth_m)):
        camera_point = rs.rs2_deproject_pixel_to_point(
            intrinsics,
            [float(x), float(y)],
            float(depth),
        )
        camera_points_h[index, :3] = camera_point

    robot_points_h = (cam_to_robot_t @ camera_points_h.T).T
    pixels_xy = np.column_stack((xs, ys)).astype(np.int32)
    return robot_points_h[:, :3], pixels_xy


def robust_median(values: np.ndarray, minimum_tolerance: float = 0.003):
    """Median with a MAD-based outlier-rejection pass."""
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


def estimate_table_z(table_ring_points: np.ndarray, object_points: np.ndarray):
    """Estimate local table Z, preferring points in the ring around the OBB."""
    if len(table_ring_points) >= MIN_TABLE_RING_POINTS:
        table_z = robust_median(table_ring_points[:, 2])
        if table_z is not None:
            return table_z, "local_ring"

    if len(object_points) >= MIN_TABLE_RING_POINTS:
        percentile = 10 if ROBOT_Z_INCREASES_UPWARD else 90
        table_candidate = np.percentile(object_points[:, 2], percentile)
        return float(table_candidate), "object_percentile_fallback"

    return None, "unavailable"


def height_above_table(z_values: np.ndarray, table_z: float) -> np.ndarray:
    if ROBOT_Z_INCREASES_UPWARD:
        return z_values - table_z
    return table_z - z_values


def get_known_object_height(class_name: str):
    normalized = normalize_name(class_name)

    if normalized in OBJECT_HEIGHTS_M:
        return OBJECT_HEIGHTS_M[normalized]

    for key in sorted(OBJECT_HEIGHTS_M, key=len, reverse=True):
        if key in normalized:
            return OBJECT_HEIGHTS_M[key]

    return None


def select_top_points_known_height(
    object_points: np.ndarray,
    table_z: float,
    object_height_m: float,
):
    """Select points close to table height plus the catalogue object height."""
    measured_heights = height_above_table(object_points[:, 2], table_z)
    selected_indices = np.where(
        np.abs(measured_heights - object_height_m)
        <= KNOWN_HEIGHT_TOLERANCE_M
    )[0]

    if len(selected_indices) < KNOWN_HEIGHT_MIN_POINTS:
        return None

    return selected_indices


def generate_auto_candidate_bands(
    object_points: np.ndarray,
    table_z: float,
):
    """
    Produce candidate upper layers from highest to lowest.

    Each layer must first have enough height support. Flatness and XY coverage
    are checked separately, so a dense side edge is not automatically accepted.
    """
    measured_heights = height_above_table(object_points[:, 2], table_z)
    above_table_indices = np.where(
        np.isfinite(measured_heights)
        & (measured_heights > TABLE_CLEARANCE_M)
    )[0]

    if len(above_table_indices) < AUTO_MIN_POINTS:
        return []

    heights = measured_heights[above_table_indices]
    high_limit = float(np.percentile(heights, AUTO_HIGH_OUTLIER_PERCENTILE))
    plausible_local = np.where(heights <= high_limit)[0]
    candidate_indices = above_table_indices[plausible_local]
    candidate_heights = measured_heights[candidate_indices]

    required_support = max(
        AUTO_MIN_POINTS,
        int(math.ceil(len(candidate_indices) * AUTO_MIN_FRACTION)),
    )

    if len(candidate_indices) < required_support:
        return []

    min_height = float(np.min(candidate_heights))
    max_height = float(np.max(candidate_heights))
    if max_height - min_height < 1e-6:
        return [candidate_indices]

    candidate_bands = []
    seen_signatures = set()

    for band_center in np.arange(
        max_height,
        min_height - AUTO_Z_BIN_SIZE_M,
        -AUTO_Z_BIN_SIZE_M,
    ):
        local = np.where(
            np.abs(candidate_heights - band_center)
            <= AUTO_BAND_HALF_WIDTH_M
        )[0]
        if len(local) < required_support:
            continue

        indices = candidate_indices[local]
        signature = (int(indices.min()), int(indices.max()), len(indices))
        if signature not in seen_signatures:
            candidate_bands.append(indices)
            seen_signatures.add(signature)

    fallback_height = float(
        np.percentile(candidate_heights, AUTO_FALLBACK_PERCENTILE)
    )
    local = np.where(
        np.abs(candidate_heights - fallback_height)
        <= KNOWN_HEIGHT_TOLERANCE_M
    )[0]
    if len(local) >= AUTO_MIN_POINTS:
        indices = candidate_indices[local]
        signature = (int(indices.min()), int(indices.max()), len(indices))
        if signature not in seen_signatures:
            candidate_bands.append(indices)

    return candidate_bands


def validate_flat_surface(points: np.ndarray):
    """
    Validate that candidate points form a sufficiently large, roughly
    horizontal plane. Returns filtered inliers plus quality metrics.
    """
    points = np.asarray(points, dtype=np.float64)
    if len(points) < AUTO_MIN_POINTS:
        return {"valid": False, "reason": "too_few_surface_points"}

    centroid = np.mean(points, axis=0)
    centered = points - centroid

    try:
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
    except np.linalg.LinAlgError:
        return {"valid": False, "reason": "plane_fit_failed"}

    normal = vh[-1]
    normal_norm = float(np.linalg.norm(normal))
    if normal_norm <= 1e-9:
        return {"valid": False, "reason": "invalid_plane_normal"}
    normal = normal / normal_norm

    distances = np.abs(centered @ normal)
    inlier_mask = distances <= SURFACE_PLANE_DISTANCE_M
    inlier_fraction = float(np.mean(inlier_mask))

    if inlier_fraction < SURFACE_MIN_INLIER_FRACTION:
        return {
            "valid": False,
            "reason": "plane_inlier_fraction_too_low",
            "inlier_fraction": inlier_fraction,
        }

    inlier_points = points[inlier_mask]
    if len(inlier_points) < AUTO_MIN_POINTS:
        return {"valid": False, "reason": "too_few_plane_inliers"}

    # Refit using inliers for more stable normal and residual measurements.
    centroid = np.mean(inlier_points, axis=0)
    centered = inlier_points - centroid
    try:
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
    except np.linalg.LinAlgError:
        return {"valid": False, "reason": "plane_refit_failed"}

    normal = vh[-1]
    normal /= max(float(np.linalg.norm(normal)), 1e-9)
    distances = np.abs(centered @ normal)

    vertical_alignment = float(abs(np.dot(normal, np.array([0.0, 0.0, 1.0]))))
    vertical_alignment = float(np.clip(vertical_alignment, 0.0, 1.0))
    tilt_deg = math.degrees(math.acos(vertical_alignment))
    p90_residual = float(np.percentile(distances, 90))

    xy = inlier_points[:, :2].astype(np.float32)
    span_x = float(np.ptp(xy[:, 0]))
    span_y = float(np.ptp(xy[:, 1]))

    if len(xy) >= 3:
        hull = cv2.convexHull(xy.reshape(-1, 1, 2))
        area_m2 = float(abs(cv2.contourArea(hull)))
    else:
        area_m2 = 0.0

    if tilt_deg > SURFACE_MAX_TILT_DEG:
        return {
            "valid": False,
            "reason": "surface_too_tilted",
            "tilt_deg": tilt_deg,
        }

    if p90_residual > SURFACE_MAX_P90_RESIDUAL_M:
        return {
            "valid": False,
            "reason": "surface_not_flat_enough",
            "p90_residual_m": p90_residual,
        }

    if min(span_x, span_y) < SURFACE_MIN_XY_SPAN_M:
        return {
            "valid": False,
            "reason": "surface_xy_span_too_small",
            "span_x_m": span_x,
            "span_y_m": span_y,
        }

    if area_m2 < SURFACE_MIN_XY_AREA_M2:
        return {
            "valid": False,
            "reason": "surface_area_too_small",
            "surface_area_m2": area_m2,
        }

    return {
        "valid": True,
        "reason": "ok",
        "points": inlier_points,
        "inlier_mask": inlier_mask,
        "inlier_fraction": inlier_fraction,
        "normal": normal,
        "tilt_deg": tilt_deg,
        "p90_residual_m": p90_residual,
        "span_x_m": span_x,
        "span_y_m": span_y,
        "surface_area_m2": area_m2,
    }


def refine_top_surface_center(
    class_name: str,
    obb_corners: np.ndarray,
    image_shape,
    depth_image: np.ndarray,
    depth_scale: float,
    intrinsics,
    method: str,
):
    """Refine an OBB into a validated robot-frame top-surface centre."""
    if not is_flat_top_class(class_name):
        return {
            "valid": False,
            "reason": "class_not_enabled_for_flat_top_refinement",
        }

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

    if len(object_points) < MIN_OBJECT_POINTS:
        return {"valid": False, "reason": "too_few_object_depth_points"}

    table_z, table_source = estimate_table_z(table_ring_points, object_points)
    if table_z is None:
        return {"valid": False, "reason": "table_height_unavailable"}

    known_height = get_known_object_height(class_name)
    method = method.lower().strip()

    def build_result(
        candidate_indices: np.ndarray,
        method_used: str,
        height_validated: bool,
    ):
        candidate_points = object_points[candidate_indices]
        surface = validate_flat_surface(candidate_points)
        if not surface["valid"]:
            return None, surface["reason"]

        # Map plane inliers back to the original candidate/object indices.
        surface_inlier_mask = surface["inlier_mask"]
        selected_indices = candidate_indices[surface_inlier_mask]
        top_points = object_points[selected_indices]
        top_pixels = object_pixels[selected_indices]

        measured_height = float(
            np.median(height_above_table(top_points[:, 2], table_z))
        )
        height_error = (
            None
            if known_height is None
            else abs(measured_height - known_height)
        )

        robot_center = np.median(top_points, axis=0)
        pixel_center = tuple(
            np.round(np.median(top_pixels, axis=0)).astype(int)
        )

        return (
            {
                "valid": True,
                "reason": "ok",
                "robot_center": robot_center,
                "pixel_center": pixel_center,
                "top_points": top_points,
                "top_pixels": top_pixels,
                "point_count": int(len(top_points)),
                "table_z": float(table_z),
                "table_source": table_source,
                "measured_height": measured_height,
                "known_height": known_height,
                "height_error": height_error,
                "height_validated": height_validated,
                "method_used": method_used,
                "inner_corners": inner_corners,
                "plane_tilt_deg": float(surface["tilt_deg"]),
                "plane_p90_residual_m": float(surface["p90_residual_m"]),
                "surface_area_m2": float(surface["surface_area_m2"]),
                "surface_inlier_fraction": float(surface["inlier_fraction"]),
            },
            "ok",
        )

    def try_auto():
        last_reason = "no_supported_auto_band"
        for candidate_indices in generate_auto_candidate_bands(
            object_points,
            table_z,
        ):
            result, reason = build_result(
                candidate_indices,
                "auto",
                height_validated=False,
            )
            if result is None:
                last_reason = reason
                continue

            if (
                VALIDATE_AUTO_WITH_KNOWN_HEIGHT
                and known_height is not None
                and result["height_error"] is not None
            ):
                if result["height_error"] > MAX_HEIGHT_ERROR_M:
                    return {
                        "valid": False,
                        "reason": "automatic_height_mismatch",
                        "measured_height": result["measured_height"],
                        "known_height": known_height,
                        "height_error": result["height_error"],
                        "inner_corners": inner_corners,
                    }
                result["height_validated"] = True
                result["method_used"] = "auto_validated"

            return result

        return {"valid": False, "reason": last_reason}

    def try_known(method_label: str):
        if known_height is None:
            return {"valid": False, "reason": "known_height_unavailable"}

        candidate_indices = select_top_points_known_height(
            object_points,
            table_z,
            known_height,
        )
        if candidate_indices is None:
            return {"valid": False, "reason": "known_height_points_unavailable"}

        result, reason = build_result(
            candidate_indices,
            method_label,
            height_validated=True,
        )
        if result is None:
            return {"valid": False, "reason": reason}
        return result

    if method == "auto":
        return try_auto()

    if method == "known_height":
        return try_known("known_height")

    if method == "hybrid":
        automatic = try_auto()
        if automatic.get("valid"):
            return automatic

        # An automatic result that forms a real plane but has an unrealistic
        # height is rejected. Do not hide the disagreement by merging or by
        # silently switching to the catalogue-based result.
        if automatic.get("reason") == "automatic_height_mismatch":
            return automatic

        known = try_known("known_height_fallback")
        if known.get("valid"):
            known["auto_failure_reason"] = automatic.get("reason")
            return known

        return {
            "valid": False,
            "reason": (
                f"auto_failed:{automatic.get('reason')};"
                f"known_failed:{known.get('reason')}"
            ),
            "inner_corners": inner_corners,
        }

    return {"valid": False, "reason": f"unknown_method:{method}"}


def local_median_depth_point(depth_frame, intrinsics, center_x: int, center_y: int):
    """Display-only fallback using a 7x7 patch instead of one depth pixel."""
    width = depth_frame.get_width()
    height = depth_frame.get_height()
    samples = []

    for y in range(max(0, center_y - 3), min(height, center_y + 4)):
        for x in range(max(0, center_x - 3), min(width, center_x + 4)):
            depth = depth_frame.get_distance(x, y)
            if (
                np.isfinite(depth)
                and MIN_VALID_DEPTH_M <= depth <= MAX_VALID_DEPTH_M
            ):
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
    global current_rgb_frame

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

    try:
        camera = profile.get_device()
        advanced_mode = rs.rs400_advanced_mode(camera)
        depth_table = advanced_mode.get_depth_table()
        depth_table.disparityShift = 20
        advanced_mode.set_depth_table(depth_table)
    except Exception as exc:
        print(f"[Vision Thread] Advanced-mode warning: {exc}")

    print("[Vision Thread] D435i started. Validated OBB top-surface refinement running.")
    print("[Controls] 1=known height, 2=automatic, 3=hybrid, z/u=zoom, q=quit")

    try:
        while True:
            frames = pipeline.wait_for_frames()
            aligned_frames = align.process(frames)

            depth_frame = aligned_frames.get_depth_frame()
            color_frame = aligned_frames.get_color_frame()
            if not depth_frame or not color_frame:
                invalidate_latest_coordinates("camera_frame_unavailable")
                continue

            color_image = np.asanyarray(color_frame.get_data())
            depth_image = np.asanyarray(depth_frame.get_data())
            with state_lock:
                current_rgb_frame = color_image.copy()
                target_class = current_target_class

            results = model(
                color_image,
                verbose=False,
                agnostic_nms=True,
                iou=0.40,
                conf=0.60,
            )

            display_focus = None
            found_any_obb = False
            best_target_candidate = None
            target_seen = False
            target_failure_reason = "target_not_detected"

            for result in results:
                if result.obb is None or len(result.obb) == 0:
                    continue

                found_any_obb = True

                for obb in result.obb:
                    cls_id = int(obb.cls[0])
                    cls_name = model.names[cls_id]
                    confidence = float(obb.conf[0])
                    is_target = class_matches_target(cls_name, target_class)
                    if is_target:
                        target_seen = True

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
                    rotation_from_matrix = CAM_TO_ROBOT_T[:3, :3]
                    roll, pitch, yaw = rotation_matrix_to_euler_angles(
                        rotation_from_matrix @ rotation_from_camera
                    )

                    cv2.polylines(color_image, [corners_int], True, (0, 255, 0), 2)
                    cv2.putText(
                        color_image,
                        f"{cls_name} {confidence:.2f}",
                        tuple(corners_int[0] + np.array([0, -10])),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (0, 255, 0),
                        2,
                    )
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

                    if refined.get("inner_corners") is not None:
                        cv2.polylines(
                            color_image,
                            [np.round(refined["inner_corners"]).astype(np.int32)],
                            True,
                            (255, 255, 0),
                            1,
                        )

                    if refined.get("valid"):
                        robot_center = refined["robot_center"]
                        refined_pixel = refined["pixel_center"]
                        display_focus = refined_pixel
                        workspace_valid = is_inside_workspace(
                            float(robot_center[0]),
                            float(robot_center[1]),
                        )

                        cv2.circle(color_image, refined_pixel, 7, (255, 0, 255), -1)

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

                        height_error_mm = (
                            None
                            if refined["height_error"] is None
                            else refined["height_error"] * 1000.0
                        )
                        error_text = (
                            "n/a"
                            if height_error_mm is None
                            else f"{height_error_mm:.1f}mm"
                        )

                        cv2.putText(
                            color_image,
                            (
                                f"TOP {refined['method_used']}: "
                                f"X:{robot_center[0]*1000:.1f} "
                                f"Y:{robot_center[1]*1000:.1f} "
                                f"Z:{robot_center[2]*1000:.1f}mm"
                            ),
                            (20, 70),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.60,
                            (255, 0, 255),
                            2,
                        )
                        cv2.putText(
                            color_image,
                            (
                                f"height:{refined['measured_height']*1000:.1f}mm "
                                f"err:{error_text} tilt:{refined['plane_tilt_deg']:.1f}deg "
                                f"pts:{refined['point_count']}"
                            ),
                            (20, 96),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.52,
                            (255, 0, 255),
                            2,
                        )

                        if not workspace_valid:
                            cv2.putText(
                                color_image,
                                "REJECTED: outside robot workspace",
                                (20, 122),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.58,
                                (0, 0, 255),
                                2,
                            )
                            if is_target:
                                target_failure_reason = "outside_robot_workspace"
                        elif is_target:
                            candidate = dict(refined)
                            candidate.update(
                                {
                                    "class_name": cls_name,
                                    "confidence": confidence,
                                    "yaw_rad": float(yaw),
                                    "roll_rad": float(roll),
                                    "pitch_rad": float(pitch),
                                }
                            )

                            # Prefer higher detector confidence, then stronger
                            # surface support when multiple target instances exist.
                            score = (confidence, refined["point_count"])
                            if (
                                best_target_candidate is None
                                or score > best_target_candidate["score"]
                            ):
                                candidate["score"] = score
                                best_target_candidate = candidate

                        print(
                            f"{cls_name} | method={refined['method_used']} | "
                            f"X={robot_center[0]*1000:.2f}mm "
                            f"Y={robot_center[1]*1000:.2f}mm "
                            f"Z={robot_center[2]*1000:.2f}mm | "
                            f"height={refined['measured_height']*1000:.2f}mm | "
                            f"tilt={refined['plane_tilt_deg']:.2f}deg | "
                            f"inside={workspace_valid}"
                        )
                    else:
                        reason = refined.get("reason", "unknown_refinement_failure")
                        if is_target:
                            target_failure_reason = reason

                        cv2.putText(
                            color_image,
                            f"Rejected {cls_name}: {reason}",
                            (20, 122),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.50,
                            (0, 0, 255),
                            2,
                        )

                        # Display-only fallback. It is deliberately not published
                        # as a safe target coordinate.
                        fallback_robot = local_median_depth_point(
                            depth_frame,
                            intrinsics,
                            obb_center_pixel[0],
                            obb_center_pixel[1],
                        )
                        if fallback_robot is not None:
                            cv2.circle(color_image, obb_center_pixel, 6, (0, 0, 255), 2)

            # Run the expensive fallback model only if no OBB was returned.
            if not found_any_obb:
                fallback_results = obb_discontinuity(
                    color_image,
                    verbose=False,
                    agnostic_nms=True,
                    iou=0.40,
                    conf=0.60,
                )

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

                        cv2.rectangle(color_image, (x1, y1), (x2, y2), (0, 0, 255), 2)
                        cv2.putText(
                            color_image,
                            f"Fallback only: {cls_name} {confidence:.2f}",
                            (x1, max(15, y1 - 10)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.5,
                            (0, 0, 255),
                            2,
                        )

                        fallback_robot = local_median_depth_point(
                            depth_frame,
                            intrinsics,
                            center_x,
                            center_y,
                        )
                        if fallback_robot is not None:
                            cv2.circle(color_image, (center_x, center_y), 5, (0, 0, 255), -1)

                        if class_matches_target(cls_name, target_class):
                            target_seen = True
                            target_failure_reason = "obb_unavailable_axis_fallback_only"

            if best_target_candidate is not None:
                best_target_candidate.pop("score", None)
                publish_latest_coordinates(best_target_candidate)
            else:
                if target_seen:
                    invalidate_latest_coordinates(target_failure_reason)
                else:
                    invalidate_latest_coordinates("target_not_detected")

            cv2.putText(
                color_image,
                f"Top mode: {top_surface_method} | 1 known  2 auto  3 hybrid",
                (20, 25),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                (255, 255, 255),
                2,
            )
            cv2.putText(
                color_image,
                "Published point = object top surface, NOT robot TCP",
                (20, 47),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.50,
                (255, 255, 255),
                1,
            )

            display_image = apply_zoom(color_image, zoom, display_focus)
            cv2.imshow("Module B: Validated OBB Top-Surface Centre", display_image)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("1"):
                top_surface_method = "known_height"
                print("[Top Surface] Switched to known-height method")
            elif key == ord("2"):
                top_surface_method = "auto"
                print("[Top Surface] Switched to automatic method")
            elif key == ord("3"):
                top_surface_method = "hybrid"
                print("[Top Surface] Switched to validated hybrid method")
            elif key == ord("z"):
                zoom = min(4.0, zoom + 0.10)
            elif key == ord("u"):
                zoom = max(1.0, zoom - 0.10)

    except Exception as exc:
        invalidate_latest_coordinates(f"vision_loop_exception:{type(exc).__name__}")
        raise
    finally:
        invalidate_latest_coordinates("vision_loop_stopped")
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
