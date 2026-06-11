def _rectangles_overlap(cx1, cy1, l1, w1, cx2, cy2, l2, w2, clearance=0.015):
    return (
        abs(cx1 - cx2) < ((l1 + l2) / 2.0 + clearance) and
        abs(cy1 - cy2) < ((w1 + w2) / 2.0 + clearance)
    )

print("Overlap exact:", _rectangles_overlap(0.3, 0.2, 0.04, 0.04, 0.3, 0.2, 0.04, 0.04))
print("Overlap slight off:", _rectangles_overlap(0.3, 0.2, 0.04, 0.04, 0.31, 0.21, 0.04, 0.04))
print("No overlap:", _rectangles_overlap(0.3, 0.2, 0.04, 0.04, 0.4, 0.2, 0.04, 0.04))
