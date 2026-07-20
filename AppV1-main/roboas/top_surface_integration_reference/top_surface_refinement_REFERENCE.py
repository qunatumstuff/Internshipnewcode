"""
top_surface_refinement.py
=========================
Pure geometry helper module for validated top-surface centre estimation.

This module contains ONLY reusable functions. It does NOT:
  - start any MCP server
  - open a RealSense pipeline
  - load any YOLO model
  - execute any robot motion
  - store a calibration matrix (receives cam_to_robot_t as an argument)

Intended usage inside vision_mcp.py run_yolo_detection():

    from top_surface_refinement import refine_top_surface_center, FLAT_TOP_CLASSES

    if cls_name in FLAT_TOP_CLASSES:
        refined = refine_top_surface_center(
            class_name=cls_name,
            obb_corners=corners_float,
            image_shape=color_image.shape,
            depth_image=depth_image_array,
            depth_scale=depth_scale,
            intrinsics=intrinsics,
            cam_to_robot_t=CAM_TO_ROBOT_T,          # pass from caller, do not copy here
            expected_height_m=catalogue_height_m,    # pass from OBJECT_CATALOGUE
            method=TOP_SURFACE_METHOD,
        )
        if refined["valid"]:
            coords["x"] = refined["x"]              # overwrite only XY initially
            coords["y"] = refined["y"]
            coords["position_source"] = refined["method_used"]
            # Attach optional metadata without removing existing keys
            coords.update(refined.get("metadata", {}))

Source:
    All core algorithm ported from outputnewjuly16_top_surface_validated.py.
    Flat-surface validation (SVD plane fit) is the primary addition over
    the older 16_07_26_10_22output_top_surface_dominic.py logic.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
import pyrealsense2 as rs

# ---------------------------------------------------------------------------
# 1. Object class allowlist — only these receive flat-top plane refinement.
#    Curved, irregular, or long objects (markers, pipe, sponge, screwdriver)
#    must not automatically go through this path.
# ---------------------------------------------------------------------------
FLAT_TOP_CLASSES = {
    "blue cube",
    "red cube",
    "green cube",
    "yellow cube",
    "cube",
    "medicine",
    "medicine box",
}

# ---------------------------------------------------------------------------
# 2. Tunable constants — prefixed with TSR_ to avoid name collisions when
#    imported into vision_mcp.py which has its own constants.
# ---------------------------------------------------------------------------
# Mask scaling
TSR_OBJECT_MASK_SCALE       = 0.92
TSR_TABLE_RING_INNER_SCALE  = 1.08
TSR_TABLE_RING_OUTER_SCALE  = 1.45

# Depth sampling
TSR_POINT_SAMPLE_STRIDE     = 2
TSR_MIN_VALID_DEPTH_M       = 0.10
TSR_MAX_VALID_DEPTH_M       = 2.00
TSR_MIN_OBJECT_POINTS       = 20
TSR_MIN_TABLE_RING_POINTS   = 15

# Table / height estimation
TSR_TABLE_CLEARANCE_M           = 0.003   # ignore points this close to table
TSR_KNOWN_HEIGHT_TOLERANCE_M    = 0.006   # ±6 mm band for known-height selection
TSR_KNOWN_HEIGHT_MIN_POINTS     = 15

# Automatic band search
TSR_AUTO_Z_BIN_SIZE_M           = 0.002
TSR_AUTO_BAND_HALF_WIDTH_M      = 0.003
TSR_AUTO_MIN_POINTS             = 20
TSR_AUTO_MIN_FRACTION           = 0.08
TSR_AUTO_HIGH_OUTLIER_PERCENTILE = 99.5
TSR_AUTO_FALLBACK_PERCENTILE    = 90.0

# Flat-surface validation (SVD plane fit)
TSR_SURFACE_PLANE_DISTANCE_M    = 0.004   # max point-to-plane distance for inlier
TSR_SURFACE_MIN_INLIER_FRACTION = 0.60
TSR_SURFACE_MAX_TILT_DEG        = 20.0
TSR_SURFACE_MAX_P90_RESIDUAL_M  = 0.0045
TSR_SURFACE_MIN_XY_SPAN_M       = 0.006
TSR_SURFACE_MIN_XY_AREA_M2      = 0.00005  # 50 mm²

# Height mismatch rejection
TSR_MAX_HEIGHT_ERROR_M          = 0.010

# Whether higher robot-base Z means physically higher in the room.
# True for standard robot arm mounting where +Z is upward.
TSR_ROBOT_Z_INCREASES_UPWARD    = True


# ---------------------------------------------------------------------------
# 3. Mask helpers
# ---------------------------------------------------------------------------

def _scale_corners(corners: np.ndarray, scale: float) -> np.ndarray:
    corners = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    center = corners.mean(axis=0)
    return center + scale * (corners - center)


def _polygon_mask(image_shape: tuple, corners: np.ndarray) -> np.ndarray:
    mask = np.zeros(image_shape[:2], dtype=np.uint8)
    cv2.fillConvexPoly(mask, np.round(corners).astype(np.int32), 255)
    return mask


def create_object_and_table_masks(
    image_shape: tuple,
    obb_corners: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Create:
      1. An inner OBB mask for collecting object depth points.
      2. A ring mask outside the OBB to estimate the local table height.

    Returns (object_mask, table_ring_mask, inner_corners).
    """
    object_corners    = _scale_corners(obb_corners, TSR_OBJECT_MASK_SCALE)
    ring_inner_corners = _scale_corners(obb_corners, TSR_TABLE_RING_INNER_SCALE)
    ring_outer_corners = _scale_corners(obb_corners, TSR_TABLE_RING_OUTER_SCALE)

    object_mask    = _polygon_mask(image_shape, object_corners)
    ring_inner     = _polygon_mask(image_shape, ring_inner_corners)
    ring_outer     = _polygon_mask(image_shape, ring_outer_corners)
    table_ring     = cv2.bitwise_and(ring_outer, cv2.bitwise_not(ring_inner))

    return object_mask, table_ring, object_corners


# ---------------------------------------------------------------------------
# 4. Depth deprojection
# ---------------------------------------------------------------------------

def mask_to_robot_points(
    mask: np.ndarray,
    depth_image: np.ndarray,
    depth_scale: float,
    intrinsics,
    cam_to_robot_t: np.ndarray,
    sample_stride: int = TSR_POINT_SAMPLE_STRIDE,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Convert valid aligned-depth pixels inside a mask into robot-frame 3D points.

    Returns:
        robot_points: shape (N, 3)
        pixels_xy:    shape (N, 2), integer pixel coordinates matching each point
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
    depth_m   = raw_depth * depth_scale

    valid_depth = (
        np.isfinite(depth_m)
        & (depth_m >= TSR_MIN_VALID_DEPTH_M)
        & (depth_m <= TSR_MAX_VALID_DEPTH_M)
    )
    xs      = xs[valid_depth]
    ys      = ys[valid_depth]
    depth_m = depth_m[valid_depth]

    if len(xs) == 0:
        return np.empty((0, 3)), np.empty((0, 2), dtype=np.int32)

    camera_points_h = np.empty((len(xs), 4), dtype=np.float64)
    camera_points_h[:, 3] = 1.0

    for idx, (px, py, d) in enumerate(zip(xs, ys, depth_m)):
        cam_pt = rs.rs2_deproject_pixel_to_point(
            intrinsics,
            [float(px), float(py)],
            float(d),
        )
        camera_points_h[idx, :3] = cam_pt

    robot_points_h = (cam_to_robot_t @ camera_points_h.T).T
    pixels_xy      = np.column_stack((xs, ys)).astype(np.int32)
    return robot_points_h[:, :3], pixels_xy


# ---------------------------------------------------------------------------
# 5. Table-height estimation
# ---------------------------------------------------------------------------

def _robust_median(values: np.ndarray, minimum_tolerance: float = 0.003) -> Optional[float]:
    """Median with MAD-based outlier rejection."""
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return None
    median    = float(np.median(values))
    mad       = float(np.median(np.abs(values - median)))
    tolerance = max(minimum_tolerance, 3.0 * 1.4826 * mad)
    filtered  = values[np.abs(values - median) <= tolerance]
    return float(np.median(filtered)) if len(filtered) > 0 else median


def estimate_local_table_z(
    table_ring_points: np.ndarray,
    object_points: np.ndarray,
) -> Tuple[Optional[float], str]:
    """
    Estimate local table Z from the ring surrounding the OBB.
    Falls back to the low percentile of the object points when the ring is sparse.

    Returns (table_z_m, source_label).
    """
    if len(table_ring_points) >= TSR_MIN_TABLE_RING_POINTS:
        table_z = _robust_median(table_ring_points[:, 2])
        if table_z is not None:
            return table_z, "local_ring"

    if len(object_points) >= TSR_MIN_TABLE_RING_POINTS:
        percentile  = 10 if TSR_ROBOT_Z_INCREASES_UPWARD else 90
        table_z     = float(np.percentile(object_points[:, 2], percentile))
        return table_z, "object_percentile_fallback"

    return None, "unavailable"


def _height_above_table(z_values: np.ndarray, table_z: float) -> np.ndarray:
    if TSR_ROBOT_Z_INCREASES_UPWARD:
        return z_values - table_z
    return table_z - z_values


# ---------------------------------------------------------------------------
# 6. Point-band selection
# ---------------------------------------------------------------------------

def _select_known_height_points(
    object_points: np.ndarray,
    table_z: float,
    object_height_m: float,
) -> Optional[np.ndarray]:
    """Select points within ±KNOWN_HEIGHT_TOLERANCE of the catalogue height."""
    measured_heights = _height_above_table(object_points[:, 2], table_z)
    selected = np.where(
        np.abs(measured_heights - object_height_m) <= TSR_KNOWN_HEIGHT_TOLERANCE_M
    )[0]
    return selected if len(selected) >= TSR_KNOWN_HEIGHT_MIN_POINTS else None


def generate_auto_candidate_bands(
    object_points: np.ndarray,
    table_z: float,
) -> List[np.ndarray]:
    """
    Produce a list of candidate point-index arrays representing the upper
    depth layers of the object, ordered from highest to lowest.

    A band needs enough numerical support before it is passed to flatness
    validation; the flatness check is separate so a dense but tilted edge
    layer does not automatically win.
    """
    measured_heights = _height_above_table(object_points[:, 2], table_z)
    above_table      = np.where(
        np.isfinite(measured_heights) & (measured_heights > TSR_TABLE_CLEARANCE_M)
    )[0]

    if len(above_table) < TSR_AUTO_MIN_POINTS:
        return []

    heights    = measured_heights[above_table]
    high_limit = float(np.percentile(heights, TSR_AUTO_HIGH_OUTLIER_PERCENTILE))
    ok_local   = np.where(heights <= high_limit)[0]
    cand_idx   = above_table[ok_local]
    cand_h     = measured_heights[cand_idx]

    required = max(TSR_AUTO_MIN_POINTS, int(math.ceil(len(cand_idx) * TSR_AUTO_MIN_FRACTION)))
    if len(cand_idx) < required:
        return []

    min_h = float(np.min(cand_h))
    max_h = float(np.max(cand_h))
    if max_h - min_h < 1e-6:
        return [cand_idx]

    bands      = []
    signatures = set()

    for band_center in np.arange(max_h, min_h - TSR_AUTO_Z_BIN_SIZE_M, -TSR_AUTO_Z_BIN_SIZE_M):
        local = np.where(np.abs(cand_h - band_center) <= TSR_AUTO_BAND_HALF_WIDTH_M)[0]
        if len(local) < required:
            continue
        indices   = cand_idx[local]
        signature = (int(indices.min()), int(indices.max()), len(indices))
        if signature not in signatures:
            bands.append(indices)
            signatures.add(signature)

    # Conservative fallback band
    fallback_h = float(np.percentile(cand_h, TSR_AUTO_FALLBACK_PERCENTILE))
    local      = np.where(np.abs(cand_h - fallback_h) <= TSR_KNOWN_HEIGHT_TOLERANCE_M)[0]
    if len(local) >= TSR_AUTO_MIN_POINTS:
        indices   = cand_idx[local]
        signature = (int(indices.min()), int(indices.max()), len(indices))
        if signature not in signatures:
            bands.append(indices)

    return bands


# ---------------------------------------------------------------------------
# 7. Flat-surface SVD validation
# ---------------------------------------------------------------------------

def validate_flat_surface(points: np.ndarray) -> Dict[str, Any]:
    """
    Validate that a candidate point set forms a sufficiently large,
    roughly horizontal plane using SVD.

    Returns a dict. Check result["valid"] first.
    On success, also contains:
        points            - inlier point array (Nx3)
        inlier_mask       - boolean mask into the original candidate array
        inlier_fraction   - float
        normal            - unit normal vector (3,)
        tilt_deg          - float
        p90_residual_m    - float
        span_x_m          - float
        span_y_m          - float
        surface_area_m2   - float
    """
    points = np.asarray(points, dtype=np.float64)
    if len(points) < TSR_AUTO_MIN_POINTS:
        return {"valid": False, "reason": "too_few_surface_points"}

    centroid = np.mean(points, axis=0)
    centered = points - centroid

    try:
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
    except np.linalg.LinAlgError:
        return {"valid": False, "reason": "plane_fit_failed"}

    normal      = vh[-1]
    normal_norm = float(np.linalg.norm(normal))
    if normal_norm <= 1e-9:
        return {"valid": False, "reason": "invalid_plane_normal"}
    normal = normal / normal_norm

    distances      = np.abs(centered @ normal)
    inlier_mask    = distances <= TSR_SURFACE_PLANE_DISTANCE_M
    inlier_frac    = float(np.mean(inlier_mask))

    if inlier_frac < TSR_SURFACE_MIN_INLIER_FRACTION:
        return {
            "valid": False,
            "reason": "plane_inlier_fraction_too_low",
            "inlier_fraction": inlier_frac,
        }

    inlier_points = points[inlier_mask]
    if len(inlier_points) < TSR_AUTO_MIN_POINTS:
        return {"valid": False, "reason": "too_few_plane_inliers"}

    # Refit on inliers for a cleaner normal and residual measurement.
    centroid = np.mean(inlier_points, axis=0)
    centered = inlier_points - centroid
    try:
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
    except np.linalg.LinAlgError:
        return {"valid": False, "reason": "plane_refit_failed"}

    normal    = vh[-1] / max(float(np.linalg.norm(vh[-1])), 1e-9)
    distances = np.abs(centered @ normal)

    vert_align = float(abs(np.dot(normal, np.array([0.0, 0.0, 1.0]))))
    vert_align = float(np.clip(vert_align, 0.0, 1.0))
    tilt_deg   = math.degrees(math.acos(vert_align))
    p90_resid  = float(np.percentile(distances, 90))

    xy     = inlier_points[:, :2].astype(np.float32)
    span_x = float(np.ptp(xy[:, 0]))
    span_y = float(np.ptp(xy[:, 1]))

    if len(xy) >= 3:
        hull     = cv2.convexHull(xy.reshape(-1, 1, 2))
        area_m2  = float(abs(cv2.contourArea(hull)))
    else:
        area_m2  = 0.0

    if tilt_deg > TSR_SURFACE_MAX_TILT_DEG:
        return {"valid": False, "reason": "surface_too_tilted", "tilt_deg": tilt_deg}
    if p90_resid > TSR_SURFACE_MAX_P90_RESIDUAL_M:
        return {"valid": False, "reason": "surface_not_flat_enough", "p90_residual_m": p90_resid}
    if min(span_x, span_y) < TSR_SURFACE_MIN_XY_SPAN_M:
        return {"valid": False, "reason": "surface_xy_span_too_small",
                "span_x_m": span_x, "span_y_m": span_y}
    if area_m2 < TSR_SURFACE_MIN_XY_AREA_M2:
        return {"valid": False, "reason": "surface_area_too_small", "surface_area_m2": area_m2}

    return {
        "valid": True,
        "reason": "ok",
        "points": inlier_points,
        "inlier_mask": inlier_mask,
        "inlier_fraction": inlier_frac,
        "normal": normal,
        "tilt_deg": tilt_deg,
        "p90_residual_m": p90_resid,
        "span_x_m": span_x,
        "span_y_m": span_y,
        "surface_area_m2": area_m2,
    }


# ---------------------------------------------------------------------------
# 8. Per-band result builder (shared by auto and known-height paths)
# ---------------------------------------------------------------------------

def _build_candidate_result(
    candidate_indices: np.ndarray,
    object_points: np.ndarray,
    object_pixels: np.ndarray,
    table_z: float,
    known_height: Optional[float],
    method_used: str,
    height_validated: bool,
    inner_corners: np.ndarray,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Validate a band of candidate indices as a flat surface and, if it passes,
    build the result dict. Returns (result_dict, reason_str).
    result_dict is None when validation fails.
    """
    candidate_points = object_points[candidate_indices]
    surface = validate_flat_surface(candidate_points)
    if not surface["valid"]:
        return None, surface["reason"]

    # Map plane inliers back into the original index space
    inlier_mask      = surface["inlier_mask"]
    selected_indices = candidate_indices[inlier_mask]
    top_points       = object_points[selected_indices]
    top_pixels       = object_pixels[selected_indices]

    measured_height = float(
        np.median(_height_above_table(top_points[:, 2], table_z))
    )
    height_error = (
        None if known_height is None
        else abs(measured_height - known_height)
    )

    robot_center = np.median(top_points, axis=0)
    pixel_center = tuple(np.round(np.median(top_pixels, axis=0)).astype(int))

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


# ---------------------------------------------------------------------------
# 9. Public entry point
# ---------------------------------------------------------------------------

def refine_top_surface_center(
    class_name: str,
    obb_corners: np.ndarray,
    image_shape: tuple,
    depth_image: np.ndarray,
    depth_scale: float,
    intrinsics,
    cam_to_robot_t: np.ndarray,
    expected_height_m: Optional[float] = None,
    method: str = "hybrid",
) -> Dict[str, Any]:
    """
    Refine an OBB detection into a validated top-surface centre in the robot
    base frame. Returns a dict; always check result["valid"] first.

    On success the dict contains:
        valid             True
        x, y, z          robot-base metres (top-surface centre, NOT TCP pose)
        method_used       "auto_validated" | "auto" | "known_height_fallback" | ...
        metadata          supplementary diagnostic fields (see below)

    On failure:
        valid             False
        reason            short string describing the failure

    The caller (vision_mcp.run_yolo_detection) is responsible for:
        - applying GRASP_OFFSETS
        - applying any permanent correction offsets
        - deciding whether to use the refined z or retain the legacy z
        - workspace boundary check (isinside)

    Parameters
    ----------
    class_name          YOLO class name (lowercase)
    obb_corners         (4, 2) float32 OBB corner pixels
    image_shape         color_image.shape (H, W, C)
    depth_image         aligned uint16 depth array from RealSense
    depth_scale         depth sensor scale factor (metres per count)
    intrinsics          rs.intrinsics from the colour stream
    cam_to_robot_t      4×4 float64 transformation matrix (from vision_mcp.CAM_TO_ROBOT_T)
    expected_height_m   catalogue object height in metres (from OBJECT_CATALOGUE)
    method              "auto" | "known_height" | "hybrid"
    """
    if class_name.strip().lower() not in FLAT_TOP_CLASSES:
        return {"valid": False, "reason": "class_not_enabled_for_flat_top_refinement"}

    object_mask, table_ring_mask, inner_corners = create_object_and_table_masks(
        image_shape, obb_corners
    )

    object_points, object_pixels = mask_to_robot_points(
        object_mask, depth_image, depth_scale, intrinsics, cam_to_robot_t
    )
    table_ring_points, _ = mask_to_robot_points(
        table_ring_mask, depth_image, depth_scale, intrinsics, cam_to_robot_t,
        sample_stride=max(2, TSR_POINT_SAMPLE_STRIDE),
    )

    if len(object_points) < TSR_MIN_OBJECT_POINTS:
        return {"valid": False, "reason": "too_few_object_depth_points"}

    table_z, _table_src = estimate_local_table_z(table_ring_points, object_points)
    if table_z is None:
        return {"valid": False, "reason": "table_height_unavailable"}

    method = method.lower().strip()

    # ── Automatic path ──────────────────────────────────────────────────────
    def _try_auto() -> Dict[str, Any]:
        last_reason = "no_supported_auto_band"
        for cand_idx in generate_auto_candidate_bands(object_points, table_z):
            result, reason = _build_candidate_result(
                cand_idx, object_points, object_pixels,
                table_z, expected_height_m,
                method_used="auto",
                height_validated=False,
                inner_corners=inner_corners,
            )
            if result is None:
                last_reason = reason
                continue

            # Validate automatic result against catalogue height when available.
            if (
                expected_height_m is not None
                and result["height_error"] is not None
                and result["height_error"] > TSR_MAX_HEIGHT_ERROR_M
            ):
                return {
                    "valid": False,
                    "reason": "automatic_height_mismatch",
                    "measured_height": result["measured_height"],
                    "known_height": expected_height_m,
                    "height_error": result["height_error"],
                    "inner_corners": inner_corners,
                }
            result["height_validated"] = (expected_height_m is not None)
            if expected_height_m is not None:
                result["method_used"] = "auto_validated"
            return result

        return {"valid": False, "reason": last_reason}

    # ── Known-height path ───────────────────────────────────────────────────
    def _try_known(label: str) -> Dict[str, Any]:
        if expected_height_m is None:
            return {"valid": False, "reason": "known_height_unavailable"}
        cand_idx = _select_known_height_points(object_points, table_z, expected_height_m)
        if cand_idx is None:
            return {"valid": False, "reason": "known_height_points_unavailable"}
        result, reason = _build_candidate_result(
            cand_idx, object_points, object_pixels,
            table_z, expected_height_m,
            method_used=label,
            height_validated=True,
            inner_corners=inner_corners,
        )
        if result is None:
            return {"valid": False, "reason": reason}
        return result

    # ── Mode dispatch ───────────────────────────────────────────────────────
    if method == "auto":
        raw = _try_auto()
    elif method == "known_height":
        raw = _try_known("known_height")
    elif method == "hybrid":
        auto_result = _try_auto()
        if auto_result.get("valid"):
            raw = auto_result
        elif auto_result.get("reason") == "automatic_height_mismatch":
            # A real plane was found but height is wrong — do NOT silently
            # fall back to catalogue result. Reject explicitly.
            raw = auto_result
        else:
            known_result = _try_known("known_height_fallback")
            if known_result.get("valid"):
                known_result["auto_failure_reason"] = auto_result.get("reason")
                raw = known_result
            else:
                raw = {
                    "valid": False,
                    "reason": (
                        f"auto_failed:{auto_result.get('reason')};"
                        f"known_failed:{known_result.get('reason')}"
                    ),
                    "inner_corners": inner_corners,
                }
    else:
        raw = {"valid": False, "reason": f"unknown_method:{method}"}

    # ── Flatten into a caller-friendly shape ────────────────────────────────
    if not raw.get("valid"):
        return raw

    robot_center = raw["robot_center"]

    # Separate diagnostic metadata from the primary coordinates so the
    # caller can optionally attach them to the detection dict without
    # risk of clobbering any core detection field.
    metadata = {
        "position_source":        raw["method_used"],
        "surface_valid":          True,
        "top_point_count":        raw["point_count"],
        "measured_height_m":      raw["measured_height"],
        "expected_height_m":      raw.get("known_height"),
        "height_error_m":         raw.get("height_error"),
        "height_validated":       raw.get("height_validated", False),
        "plane_tilt_deg":         raw["plane_tilt_deg"],
        "plane_p90_residual_m":   raw["plane_p90_residual_m"],
        "surface_area_m2":        raw["surface_area_m2"],
        "surface_inlier_fraction": raw["surface_inlier_fraction"],
    }

    return {
        "valid":          True,
        "reason":         "ok",
        "x":              float(robot_center[0]),
        "y":              float(robot_center[1]),
        "z":              float(robot_center[2]),       # top-surface Z — see NOTE below
        "pixel_center":   raw["pixel_center"],
        "top_pixels":     raw["top_pixels"],
        "inner_corners":  raw["inner_corners"],
        "method_used":    raw["method_used"],
        "metadata":       metadata,
        # NOTE on z:
        #   This is the top-surface Z in the robot base frame.
        #   The current _pixel_to_robot() in vision_mcp.py adds (height_m / 2)
        #   to shift from top-surface to object-centre.  Do NOT use this z
        #   directly as the TCP target until the downstream contract has been
        #   verified against nogripperref.py and physical hover tests.
        #   Use refined x, y immediately; validate z separately.
    }
