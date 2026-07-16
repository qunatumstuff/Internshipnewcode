"""
Z-only calibration recorder and correction builder.

Purpose
-------
This script records the current camera-predicted robot-frame XYZ for the same
object at several workspace positions. It assumes the correct object-top Z is
constant at 15 mm and does NOT require manually entered robot X/Y coordinates.

Press:
    C = record one stable sample
    R = clear the recent detection buffer
    K = calculate and save the Z-only correction
    Q = quit

The correction model is:
    corrected_z = predicted_z + a*predicted_x + b*predicted_y + c

X and Y are kept unchanged. They are used only to determine where the object is
in the workspace so the Z correction can vary smoothly with position.

Important reference-frame note
------------------------------
EXPECTED_Z_M = 0.015 means the corrected object top will read Z = 15 mm.
That is suitable when you intentionally want a table-relative Z frame where the
table is Z = 0. It is NOT automatically the same as robot-base Z.
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


# -----------------------------------------------------------------------------
# 1. User settings
# -----------------------------------------------------------------------------
TARGET_CLASS = "blue cube"
OBB_MODEL_PATH = "best (12).pt"

# Desired constant top-surface Z.
EXPECTED_Z_M = 0.03  # 30 mm

OUTPUT_DIR = Path("z_only_calibration")
CSV_PATH = OUTPUT_DIR / "z_only_samples.csv"

STREAM_WIDTH = 640
STREAM_HEIGHT = 480
STREAM_FPS = 30

YOLO_CONFIDENCE = 0.60
YOLO_IOU = 0.40

DEPTH_RADIUS = 4
HISTORY_LENGTH = 15
MIN_HISTORY_FOR_CAPTURE = 5
MIN_SAMPLES_FOR_FIT = 5


# Current camera-to-robot matrix from output3.py.
CAM_TO_ROBOT_T = np.array([
    [0.7389493262, 0.5903177251, -0.3247751171, 0.7326856827],
[0.6725179732, -0.6755053173, 0.3023444095, -0.4961713772],
[-0.0409080545, -0.4418343012, -0.8961634791, 0.8225271939],
[0.0000000000, 0.0000000000, 0.0000000000, 1.0000000000],
], dtype=np.float64)


# -----------------------------------------------------------------------------
# 2. Depth and CSV helpers
# -----------------------------------------------------------------------------
def get_median_depth(depth_frame, center_x, center_y, radius=4):
    """Return median valid depth from a square region around the object centre."""
    valid_depths = []

    width = depth_frame.get_width()
    height = depth_frame.get_height()

    for y in range(
        max(0, center_y - radius),
        min(height, center_y + radius + 1),
    ):
        for x in range(
            max(0, center_x - radius),
            min(width, center_x + radius + 1),
        ):
            depth = depth_frame.get_distance(x, y)

            if np.isfinite(depth) and depth > 0.0:
                valid_depths.append(depth)

    if not valid_depths:
        return None

    return float(np.median(valid_depths))


def ensure_csv_exists():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if CSV_PATH.exists():
        return

    with CSV_PATH.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow([
            "sample_id",
            "timestamp",
            "class_name",
            "confidence",
            "pixel_x",
            "pixel_y",
            "depth_m",
            "predicted_x",
            "predicted_y",
            "predicted_z",
            "true_x_placeholder",
            "true_y_placeholder",
            "expected_z",
            "image_file",
        ])


def next_sample_id():
    if not CSV_PATH.exists():
        return 1

    with CSV_PATH.open("r", newline="", encoding="utf-8") as file:
        number_of_rows = sum(1 for _ in file)

    # One row is the CSV header.
    return max(1, number_of_rows)


def save_sample(
    frame,
    class_name,
    confidence,
    pixel_x,
    pixel_y,
    depth_m,
    predicted_xyz,
):
    sample_id = next_sample_id()
    image_name = f"sample_{sample_id:03d}.jpg"
    image_path = OUTPUT_DIR / image_name

    cv2.imwrite(str(image_path), frame)

    with CSV_PATH.open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow([
            sample_id,
            time.strftime("%Y-%m-%d %H:%M:%S"),
            class_name,
            f"{confidence:.6f}",
            pixel_x,
            pixel_y,
            f"{depth_m:.10f}",
            f"{predicted_xyz[0]:.10f}",
            f"{predicted_xyz[1]:.10f}",
            f"{predicted_xyz[2]:.10f}",
            "",  # X placeholder: intentionally not measured
            "",  # Y placeholder: intentionally not measured
            f"{EXPECTED_Z_M:.10f}",
            image_name,
        ])

    return sample_id, image_path


def load_z_samples():
    predicted_points = []
    expected_z_values = []

    with CSV_PATH.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)

        for row_number, row in enumerate(reader, start=2):
            try:
                predicted_points.append([
                    float(row["predicted_x"]),
                    float(row["predicted_y"]),
                    float(row["predicted_z"]),
                ])
                expected_z_values.append(float(row["expected_z"]))
            except (TypeError, ValueError, KeyError) as exc:
                raise ValueError(
                    f"Invalid calibration data on CSV row {row_number}: {row}"
                ) from exc

    predicted_array = np.asarray(predicted_points, dtype=np.float64)
    expected_z_array = np.asarray(expected_z_values, dtype=np.float64)

    if len(predicted_array) < MIN_SAMPLES_FOR_FIT:
        raise ValueError(
            f"Need at least {MIN_SAMPLES_FOR_FIT} samples; "
            f"currently have {len(predicted_array)}."
        )

    design = np.column_stack([
        predicted_array[:, 0],
        predicted_array[:, 1],
        np.ones(len(predicted_array)),
    ])

    if np.linalg.matrix_rank(design) < 3:
        raise ValueError(
            "Samples do not cover enough X/Y area. "
            "Use points spread across the workspace, not a single line."
        )

    return predicted_array, expected_z_array


# -----------------------------------------------------------------------------
# 3. Fit and save the Z-only correction
# -----------------------------------------------------------------------------
def calculate_and_save_z_correction():
    predicted, expected_z = load_z_samples()

    predicted_x = predicted[:, 0]
    predicted_y = predicted[:, 1]
    predicted_z = predicted[:, 2]

    # Required amount to add at each sample.
    z_error = expected_z - predicted_z

    # Fit:
    # z_error = a*x + b*y + c
    design = np.column_stack([
        predicted_x,
        predicted_y,
        np.ones(len(predicted)),
    ])

    a, b, c = np.linalg.lstsq(
        design,
        z_error,
        rcond=None,
    )[0]

    corrected_z = (
        predicted_z
        + a * predicted_x
        + b * predicted_y
        + c
    )

    before_error_mm = (predicted_z - expected_z) * 1000.0
    after_error_mm = (corrected_z - expected_z) * 1000.0

    before_rmse_mm = float(np.sqrt(np.mean(before_error_mm ** 2)))
    after_rmse_mm = float(np.sqrt(np.mean(after_error_mm ** 2)))
    after_max_abs_mm = float(np.max(np.abs(after_error_mm)))

    # Affine Z-only correction:
    # X' = X
    # Y' = Y
    # Z' = Z + aX + bY + c
    Z_CORRECTION_T = np.array([
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [a,   b,   1.0, c],
        [0.0, 0.0, 0.0, 1.0],
    ], dtype=np.float64)

    Z_CORRECTED_CAM_TO_ROBOT_T = (
        Z_CORRECTION_T @ CAM_TO_ROBOT_T
    )

    np.save(
        OUTPUT_DIR / "Z_CORRECTION_T.npy",
        Z_CORRECTION_T,
    )
    np.save(
        OUTPUT_DIR / "CAM_TO_ROBOT_T_z_corrected.npy",
        Z_CORRECTED_CAM_TO_ROBOT_T,
    )

    np.savetxt(
        OUTPUT_DIR / "Z_CORRECTION_T.txt",
        Z_CORRECTION_T,
        fmt="%.10f",
    )
    np.savetxt(
        OUTPUT_DIR / "CAM_TO_ROBOT_T_z_corrected.txt",
        Z_CORRECTED_CAM_TO_ROBOT_T,
        fmt="%.10f",
    )

    coefficient_text = (
        f"EXPECTED_Z_M = {EXPECTED_Z_M:.10f}\n"
        f"Z_CORRECTION_A = {a:.10f}\n"
        f"Z_CORRECTION_B = {b:.10f}\n"
        f"Z_CORRECTION_C = {c:.10f}\n"
        f"BEFORE_RMSE_MM = {before_rmse_mm:.4f}\n"
        f"AFTER_RMSE_MM = {after_rmse_mm:.4f}\n"
        f"AFTER_MAX_ABS_ERROR_MM = {after_max_abs_mm:.4f}\n"
    )

    (OUTPUT_DIR / "z_correction_coefficients.txt").write_text(
        coefficient_text,
        encoding="utf-8",
    )

    print("\n[Z Correction] Coefficients")
    print(f"a = {a:.10f}")
    print(f"b = {b:.10f}")
    print(f"c = {c:.10f}")

    print("\n[Z Correction] Error")
    print(f"Before RMSE: {before_rmse_mm:.3f} mm")
    print(f"After RMSE:  {after_rmse_mm:.3f} mm")
    print(f"After maximum absolute error: {after_max_abs_mm:.3f} mm")

    print("\n[Z Correction] Z_CORRECTION_T")
    print(Z_CORRECTION_T)

    print("\n[Z Correction] CAM_TO_ROBOT_T_z_corrected")
    print(Z_CORRECTED_CAM_TO_ROBOT_T)

    print(f"\n[Saved] {OUTPUT_DIR.resolve()}")


# -----------------------------------------------------------------------------
# 4. Recorder loop
# -----------------------------------------------------------------------------
def main():
    ensure_csv_exists()

    model = YOLO(OBB_MODEL_PATH)

    pipeline = rs.pipeline()
    config = rs.config()

    config.enable_stream(
        rs.stream.depth,
        STREAM_WIDTH,
        STREAM_HEIGHT,
        rs.format.z16,
        STREAM_FPS,
    )
    config.enable_stream(
        rs.stream.color,
        STREAM_WIDTH,
        STREAM_HEIGHT,
        rs.format.bgr8,
        STREAM_FPS,
    )

    profile = pipeline.start(config)
    align = rs.align(rs.stream.color)

    intrinsics = (
        profile
        .get_stream(rs.stream.color)
        .as_video_stream_profile()
        .get_intrinsics()
    )

    xyz_history = deque(maxlen=HISTORY_LENGTH)
    depth_history = deque(maxlen=HISTORY_LENGTH)
    pixel_history = deque(maxlen=HISTORY_LENGTH)
    confidence_history = deque(maxlen=HISTORY_LENGTH)

    print("[Z-only recorder] Started.")
    print(f"[Target] {TARGET_CLASS}")
    print(f"[Expected Z] {EXPECTED_Z_M * 1000:.1f} mm")
    print("[Controls] C=record, R=clear buffer, K=fit correction, Q=quit")
    print(f"[CSV] {CSV_PATH.resolve()}")

    try:
        while True:
            frames = pipeline.wait_for_frames()
            aligned_frames = align.process(frames)

            depth_frame = aligned_frames.get_depth_frame()
            color_frame = aligned_frames.get_color_frame()

            if not depth_frame or not color_frame:
                continue

            color_image = np.asanyarray(color_frame.get_data())
            display_image = color_image.copy()

            results = model(
                color_image,
                verbose=False,
                agnostic_nms=True,
                iou=YOLO_IOU,
                conf=YOLO_CONFIDENCE,
            )

            best_obb = None
            best_confidence = -1.0
            detected_class = None

            for result in results:
                if result.obb is None:
                    continue

                for obb in result.obb:
                    class_id = int(obb.cls[0])
                    class_name = model.names[class_id].lower()
                    confidence = float(obb.conf[0])

                    if class_name != TARGET_CLASS.lower():
                        continue

                    if confidence > best_confidence:
                        best_obb = obb
                        best_confidence = confidence
                        detected_class = class_name

            stable_xyz = None
            stable_depth = None
            stable_pixel = None
            stable_confidence = None

            if best_obb is not None:
                box_points = (
                    best_obb.xyxyxyxy[0]
                    .cpu()
                    .numpy()
                    .astype(np.int32)
                )

                center_x = int(best_obb.xywhr[0][0])
                center_y = int(best_obb.xywhr[0][1])

                depth_m = get_median_depth(
                    depth_frame,
                    center_x,
                    center_y,
                    radius=DEPTH_RADIUS,
                )

                cv2.polylines(
                    display_image,
                    [box_points],
                    True,
                    (0, 255, 0),
                    2,
                )

                cv2.putText(
                    display_image,
                    f"{detected_class} {best_confidence:.2f}",
                    tuple(box_points[0]),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0),
                    2,
                )

                if depth_m is not None:
                    camera_xyz = rs.rs2_deproject_pixel_to_point(
                        intrinsics,
                        [center_x, center_y],
                        depth_m,
                    )

                    robot_h = CAM_TO_ROBOT_T @ np.array([
                        camera_xyz[0],
                        camera_xyz[1],
                        camera_xyz[2],
                        1.0,
                    ], dtype=np.float64)

                    predicted_xyz = robot_h[:3]

                    xyz_history.append(predicted_xyz)
                    depth_history.append(depth_m)
                    pixel_history.append([center_x, center_y])
                    confidence_history.append(best_confidence)

                    stable_xyz = np.median(
                        np.stack(xyz_history),
                        axis=0,
                    )
                    stable_depth = float(np.median(depth_history))
                    stable_pixel = np.median(
                        np.asarray(pixel_history),
                        axis=0,
                    ).astype(int)
                    stable_confidence = float(
                        np.median(confidence_history)
                    )

                    cv2.circle(
                        display_image,
                        (center_x, center_y),
                        4,
                        (0, 0, 255),
                        -1,
                    )

                    cv2.putText(
                        display_image,
                        (
                            f"Predicted XYZ mm: "
                            f"{stable_xyz[0] * 1000:.2f}, "
                            f"{stable_xyz[1] * 1000:.2f}, "
                            f"{stable_xyz[2] * 1000:.2f}"
                        ),
                        (20, 35),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.65,
                        (0, 0, 255),
                        2,
                    )

                    cv2.putText(
                        display_image,
                        (
                            f"Expected Z: {EXPECTED_Z_M * 1000:.1f} mm  "
                            f"Buffer: {len(xyz_history)}/{HISTORY_LENGTH}"
                        ),
                        (20, 65),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        (0, 255, 255),
                        2,
                    )
                else:
                    cv2.putText(
                        display_image,
                        "Invalid depth near object centre",
                        (20, 35),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.65,
                        (0, 0, 255),
                        2,
                    )
            else:
                cv2.putText(
                    display_image,
                    f"Waiting for {TARGET_CLASS}",
                    (20, 35),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (0, 255, 255),
                    2,
                )

            cv2.imshow(
                "Z-only Calibration Recorder",
                display_image,
            )

            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                break

            if key == ord("r"):
                xyz_history.clear()
                depth_history.clear()
                pixel_history.clear()
                confidence_history.clear()
                print("[Recorder] Buffer cleared.")

            if key == ord("c"):
                if stable_xyz is None:
                    print("[Recorder] No valid target pose to save.")
                    continue

                if len(xyz_history) < MIN_HISTORY_FOR_CAPTURE:
                    print(
                        "[Recorder] Wait for more stable readings "
                        f"({len(xyz_history)}/{MIN_HISTORY_FOR_CAPTURE})."
                    )
                    continue

                sample_id, image_path = save_sample(
                    frame=display_image,
                    class_name=detected_class,
                    confidence=stable_confidence,
                    pixel_x=int(stable_pixel[0]),
                    pixel_y=int(stable_pixel[1]),
                    depth_m=stable_depth,
                    predicted_xyz=stable_xyz,
                )

                print(
                    f"[Saved {sample_id}] "
                    f"Predicted XYZ = "
                    f"({stable_xyz[0]:.6f}, "
                    f"{stable_xyz[1]:.6f}, "
                    f"{stable_xyz[2]:.6f})"
                )
                print(
                    f"[Saved {sample_id}] "
                    f"Expected Z = {EXPECTED_Z_M:.6f}"
                )
                print(f"[Saved {sample_id}] Image: {image_path}")

                # Start fresh at the next workspace position.
                xyz_history.clear()
                depth_history.clear()
                pixel_history.clear()
                confidence_history.clear()

            if key == ord("k"):
                try:
                    calculate_and_save_z_correction()
                except Exception as exc:
                    print(f"[Z Correction] Failed: {exc}")

    finally:
        pipeline.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
