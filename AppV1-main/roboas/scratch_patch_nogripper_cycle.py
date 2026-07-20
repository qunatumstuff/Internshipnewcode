import re

def insert_check_safety():
    with open("nogripperref.py", "r", encoding="utf-8") as f:
        content = f.read()

    # I will do targeted substitutions on the trajectory execution calls to insert check_safety()
    # It's safest to just replace the whole block.
    old_block = """    execute_joint_transit(r, home, lift_pick_forward, label="Phase 1 transit ?" Home -> lift_pick_forward")
    
    execute_trajectory(r, phase1_rotate, label="Phase 1 wrist rotate ?" forward -> grip angle", custom_speed=0.5, is_blending=False)
    time.sleep(0.3)

    # Open 30% larger than the object before descending.
    gripper_open_for_object(OBJECT_GRIP_WIDTH_M)

    execute_trajectory(r, phase1_approach, label="Phase 1 approach ?" lift_pick -> pick_pose")
    gripper_grip_object(OBJECT_GRIP_WIDTH_M)

    CARRIED_OBJECT_ENABLED = True

    execute_trajectory(r, phase2_depart, label="Phase 2 depart ?" pick_pose -> lift_pick")
    
    execute_trajectory(r, phase2_reorient, label="Phase 2 reorient ?" pick angle -> forward", custom_speed=0.5, is_blending=False)
    execute_joint_transit(r, lift_pick_forward_after, lift_drop, label="Phase 2 transit ?" pick_forward -> drop_forward")
    execute_trajectory(r, [lift_drop, lift_drop_grip], label="Phase 2 reorient ?" forward -> drop angle", custom_speed=0.5, is_blending=False)
    
    execute_trajectory(r, phase2_approach, label="Phase 2 approach ?" lift_drop -> drop_pose")
    gripper_release_object(OBJECT_GRIP_WIDTH_M)

    CARRIED_OBJECT_ENABLED = False

    execute_trajectory(r, phase3_depart, label="Phase 3 depart ?" drop_pose -> lift_drop")"""

    new_block = """    execute_joint_transit(r, home, lift_pick_forward, label="Phase 1 transit ?" Home -> lift_pick_forward")
    
    check_safety()
    execute_trajectory(r, phase1_rotate, label="Phase 1 wrist rotate ?" forward -> grip angle", custom_speed=0.5, is_blending=False)
    time.sleep(0.3)

    # Open 30% larger than the object before descending.
    check_safety()
    gripper_open_for_object(OBJECT_GRIP_WIDTH_M)

    check_safety()
    execute_trajectory(r, phase1_approach, label="Phase 1 approach ?" lift_pick -> pick_pose")
    check_safety()
    gripper_grip_object(OBJECT_GRIP_WIDTH_M)

    CARRIED_OBJECT_ENABLED = True

    check_safety()
    execute_trajectory(r, phase2_depart, label="Phase 2 depart ?" pick_pose -> lift_pick")
    
    check_safety()
    execute_trajectory(r, phase2_reorient, label="Phase 2 reorient ?" pick angle -> forward", custom_speed=0.5, is_blending=False)
    check_safety()
    execute_joint_transit(r, lift_pick_forward_after, lift_drop, label="Phase 2 transit ?" pick_forward -> drop_forward")
    check_safety()
    execute_trajectory(r, [lift_drop, lift_drop_grip], label="Phase 2 reorient ?" forward -> drop angle", custom_speed=0.5, is_blending=False)
    
    check_safety()
    execute_trajectory(r, phase2_approach, label="Phase 2 approach ?" lift_drop -> drop_pose")
    check_safety()
    gripper_release_object(OBJECT_GRIP_WIDTH_M)

    CARRIED_OBJECT_ENABLED = False

    check_safety()
    execute_trajectory(r, phase3_depart, label="Phase 3 depart ?" drop_pose -> lift_drop")"""
    
    content = content.replace(old_block, new_block)

    with open("nogripperref.py", "w", encoding="utf-8") as f:
        f.write(content)

if __name__ == "__main__":
    insert_check_safety()
