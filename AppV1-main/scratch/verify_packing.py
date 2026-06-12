import os
import sys
from types import ModuleType

# 1. Stub the physical robot neurapy dependency dynamically
print("Stubbing 'neurapy' module dependencies...")
class MockRobot:
    def __init__(self):
        pass
    def stop(self):
        pass

neurapy_mod = ModuleType("neurapy")
neurapy_robot_mod = ModuleType("neurapy.robot")
neurapy_robot_mod.Robot = MockRobot
sys.modules["neurapy"] = neurapy_mod
sys.modules["neurapy.robot"] = neurapy_robot_mod

# Ensure the roboas folder is in system path
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(THIS_DIR)
sys.path.append(os.path.join(WORKSPACE_ROOT, "roboas"))

print("Importing robot control module...")
try:
    import nogripperref as robot_control
    print("Successfully imported nogripperref.py!")
except Exception as e:
    print(f"Error importing nogripperref.py: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Verify new parameters are correctly set
print("\n--- Verifying Gripper Parameter Upgrades ---")
print(f"GRIPPER_LEN_OPEN:                 {robot_control.GRIPPER_LEN_OPEN} m (expected: 0.145)")
print(f"GRIPPER_LEN_CLOSED:               {robot_control.GRIPPER_LEN_CLOSED} m (expected: 0.145)")
print(f"FLANGE_LENGTH_M:                  {robot_control.FLANGE_LENGTH_M} m (expected: 0.014)")
print(f"FLANGE_DIAMETER_M:                {robot_control.FLANGE_DIAMETER_M} m (expected: 0.089 = 0.084 + 0.005)")
print(f"NECK_LENGTH_M:                    {robot_control.NECK_LENGTH_M} m (expected: 0.085)")
print(f"NECK_DIAMETER_M:                  {robot_control.NECK_DIAMETER_M} m (expected: 0.095 = 0.090 + 0.005)")
print(f"MAX_STROKE_M:                     {robot_control.MAX_STROKE_M} m (expected: 0.038)")
print(f"MAX_PHYSICAL_GRIPPER_WIDTH_M:     {robot_control.MAX_PHYSICAL_GRIPPER_WIDTH_M} m (expected: 0.156)")
print(f"GRIPPER_PHYSICAL_CLOSED_LENGTH_M: {robot_control.GRIPPER_PHYSICAL_CLOSED_LENGTH_M} m (expected: 0.118)")
print(f"GRIPPER_PHYSICAL_OPEN_LENGTH_M:   {robot_control.GRIPPER_PHYSICAL_OPEN_LENGTH_M} m (expected: 0.156)")

assert robot_control.GRIPPER_LEN_OPEN == 0.145, "Mismatch in GRIPPER_LEN_OPEN!"
assert robot_control.MAX_STROKE_M == 0.038, "Mismatch in MAX_STROKE_M!"

# Test drop slot allocation loop for several objects
print("\n--- Testing Drop Slot Allocation Loop ---")
objects_to_place = ["cube", "medicine", "nut", "sponge", "cube"]

robot_control.PLACED_OBJECTS = []  # clear box

for idx, name in enumerate(objects_to_place):
    print(f"\nAllocating slot {idx+1} for: '{name}'")
    try:
        obj_profile = robot_control.select_object_profile_by_name(name)
        slot = robot_control.allocate_drop_slot_for_object(obj_profile)
        print(f"SUCCESS: Allocated slot -> X: {slot['x']:.4f}, Y: {slot['y']:.4f}")
        print(f"         Dimensions -> length_m: {slot['length_m']:.4f}, width_m: {slot['width_m']:.4f}")
        print(f"         Orientation -> rotated: {slot['rotated']}, placement_angle_deg: {slot['placement_angle_deg']:.2f}°")
        
        # Test runtime variable resolution
        runtime = robot_control.resolve_object_runtime_variables(obj_profile, 0.4, -0.2, slot)
        print(f"         Resolved drop TCP yaw (DROP_RZ_DEG): {runtime['DROP_RZ_DEG']:.2f}° (pick was: {runtime['PICK_RZ_DEG']:.2f}°)")
        
        if slot["rotated"]:
            # Check if 90 degrees offset is present in drop_rz relative to base plus placement offset
            expected_base = robot_control.planned_rz_for_object(obj_profile)
            offset_diff = robot_control._normalise_angle_deg(slot["placement_angle_deg"] - expected_base)
            print(f"         Rotation verification: placement_angle offset is {offset_diff:.2f}°")
    except Exception as e:
        print(f"Error allocating slot for {name}: {e}")
        import traceback
        traceback.print_exc()

print("\nAll verification checks complete!")
