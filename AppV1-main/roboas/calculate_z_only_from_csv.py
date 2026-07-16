"""
Calculate an X/Y-dependent Z correction from z_only_samples.csv.

Expected CSV columns:
    predicted_x, predicted_y, predicted_z, expected_z

Run:
    python calculate_z_only_from_csv.py

Place this script and z_only_samples.csv in the same folder.
"""

from pathlib import Path
import csv
import numpy as np


CSV_PATH = Path(__file__).with_name("z_only_samples.csv")

CAM_TO_ROBOT_T = np.array([
    [0.7389493262, 0.5903177251, -0.3247751171, 0.7326856827],
[0.6725179732, -0.6755053173, 0.3023444095, -0.4961713772],
[-0.0409080545, -0.4418343012, -0.8961634791, 0.8225271939],
[0.0000000000, 0.0000000000, 0.0000000000, 1.0000000000],
], dtype=np.float64)


def load_samples(csv_path):
    points = []
    expected_z_values = []

    with csv_path.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)

        required = {
            "predicted_x",
            "predicted_y",
            "predicted_z",
            "expected_z",
        }

        if reader.fieldnames is None:
            raise ValueError("CSV has no header.")

        missing = required.difference(reader.fieldnames)
        if missing:
            raise ValueError(f"Missing CSV columns: {sorted(missing)}")

        for row_number, row in enumerate(reader, start=2):
            try:
                points.append([
                    float(row["predicted_x"]),
                    float(row["predicted_y"]),
                    float(row["predicted_z"]),
                ])
                expected_z_values.append(float(row["expected_z"]))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid number on CSV row {row_number}: {row}"
                ) from exc

    predicted = np.asarray(points, dtype=np.float64)
    expected_z = np.asarray(expected_z_values, dtype=np.float64)

    if len(predicted) < 5:
        raise ValueError("Use at least 5 samples.")

    design = np.column_stack([
        predicted[:, 0],
        predicted[:, 1],
        np.ones(len(predicted)),
    ])

    if np.linalg.matrix_rank(design) < 3:
        raise ValueError(
            "Samples must cover a 2D area. Do not record them all on one line."
        )

    return predicted, expected_z


def main():
    if not CSV_PATH.exists():
        raise FileNotFoundError(
            f"Could not find {CSV_PATH}. Put the CSV beside this script."
        )

    predicted, expected_z = load_samples(CSV_PATH)

    x = predicted[:, 0]
    y = predicted[:, 1]
    z = predicted[:, 2]

    z_error = expected_z - z

    design = np.column_stack([
        x,
        y,
        np.ones(len(predicted)),
    ])

    a, b, c = np.linalg.lstsq(
        design,
        z_error,
        rcond=None,
    )[0]

    corrected_z = z + a * x + b * y + c

    before_error_mm = (z - expected_z) * 1000.0
    after_error_mm = (corrected_z - expected_z) * 1000.0

    before_rmse_mm = float(
        np.sqrt(np.mean(before_error_mm ** 2))
    )
    after_rmse_mm = float(
        np.sqrt(np.mean(after_error_mm ** 2))
    )
    max_after_error_mm = float(
        np.max(np.abs(after_error_mm))
    )

    z_correction_t = np.array([
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [a,   b,   1.0, c],
        [0.0, 0.0, 0.0, 1.0],
    ], dtype=np.float64)

    corrected_cam_to_robot_t = (
        z_correction_t @ CAM_TO_ROBOT_T
    )

    print(f"Loaded {len(predicted)} samples.")
    print("\nCorrection equation:")
    print(
        "corrected_z = predicted_z "
        f"+ ({a:.10f})*x "
        f"+ ({b:.10f})*y "
        f"+ ({c:.10f})"
    )

    print("\nBefore correction:")
    print(f"Z RMSE = {before_rmse_mm:.3f} mm")

    print("\nAfter correction:")
    print(f"Z RMSE = {after_rmse_mm:.3f} mm")
    print(f"Maximum absolute Z error = {max_after_error_mm:.3f} mm")

    print("\nZ_CORRECTION_T:")
    print(np.array2string(
        z_correction_t,
        precision=10,
        suppress_small=False,
    ))

    print("\nCAM_TO_ROBOT_T_z_corrected:")
    print(np.array2string(
        corrected_cam_to_robot_t,
        precision=10,
        suppress_small=False,
    ))

    np.savetxt(
        Path(__file__).with_name("Z_CORRECTION_T.txt"),
        z_correction_t,
        fmt="%.10f",
    )

    np.savetxt(
        Path(__file__).with_name(
            "CAM_TO_ROBOT_T_z_corrected.txt"
        ),
        corrected_cam_to_robot_t,
        fmt="%.10f",
    )

    print("\nSaved:")
    print("Z_CORRECTION_T.txt")
    print("CAM_TO_ROBOT_T_z_corrected.txt")


if __name__ == "__main__":
    main()
