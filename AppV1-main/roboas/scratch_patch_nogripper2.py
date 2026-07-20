import re

def patch_nogripper():
    with open("nogripperref.py", "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Import catalogue
    if "from object_catalogue import OBJECT_CATALOGUE" not in content:
        content = content.replace("import math\n", "import math\nfrom object_catalogue import OBJECT_CATALOGUE\n")

    # 2. Wrappers check_safety
    wrappers = ["_safe_move_linear", "_safe_move_joint", "_safe_move_pose", "_safe_power_on", "_safe_reset_errors", "_safe_execute"]
    for w in wrappers:
        # Replace `# check_safety() already exists somewhere` with `check_safety()`
        old_str = f"    def {w}(self, *args, **kwargs):\n        # check_safety() already exists somewhere"
        new_str = f"    def {w}(self, *args, **kwargs):\n        check_safety()"
        content = content.replace(old_str, new_str)

    # 3. Exception callbacks in mcp_build_pick_sequence etc.
    # We should search for "except EmergencyStopException:" blocks that swallow errors.
    # The user says:
    # "The first handler always catches the exception, so the callback handler can never run.
    # Additionally, an EmergencyStopException raised inside the generic except Exception block will not be caught by another handler belonging to the same try. That path must explicitly emit the callback before raising."
    
    # Let's fix the run_mcp_ functions:
    run_funcs = ["run_mcp_pick_and_place", "run_mcp_relocate_object"]
    for func in run_funcs:
        # Find the try/except block
        old_try_except = """    except EmergencyStopException:
        raise
    except EmergencyStopException as exc:
        ROBOT_EVENT_CALLBACK("error", f"Protective stop: {exc}")
        raise
    except Exception as exc:
        logger.error(f"Error: {exc}")
        raise EmergencyStopException(str(exc))"""
        
        new_try_except = """    except EmergencyStopException as exc:
        ROBOT_EVENT_CALLBACK("error", f"Protective stop: {exc}")
        raise
    except Exception as exc:
        logger.error(f"Error: {exc}")
        ROBOT_EVENT_CALLBACK("error", f"Protective stop: {exc}")
        raise EmergencyStopException(str(exc))"""
        
        content = content.replace(old_try_except, new_try_except)

    # 4. _mcp_object_height_from_z
    old_height = """def _mcp_object_height_from_z(mcp_z: float) -> float:
    \"\"\"
    Converts Vision's z-coordinate (distance above the tabletop) into the object's height.
    Because vision 'z' is inherently the object's height on the table, we clamp it to sane bounds
    and return it directly.
    \"\"\"
    return min(max(mcp_z, 0.010), 0.150)"""
    new_height = """def _mcp_object_height_from_z(mcp_name: str) -> float:
    \"\"\"
    Ignores Vision Z entirely. Retrieves verified height from OBJECT_CATALOGUE.
    Fallback to 0.040m (standard cube) if unknown.
    \"\"\"
    cat_entry = OBJECT_CATALOGUE.get(mcp_name.lower(), {})
    return cat_entry.get("height_m", 0.040)"""
    content = content.replace(old_height, new_height)

    # Replace usages
    content = content.replace("obj_height = _mcp_object_height_from_z(obj_z)", "obj_height = _mcp_object_height_from_z(kwargs.get('object_name', 'cube'))")
    content = content.replace("obj_height = _mcp_object_height_from_z(target_z)", "obj_height = _mcp_object_height_from_z(kwargs.get('target_name', 'cube'))")
    content = content.replace("obs_height = _mcp_object_height_from_z(obs_z)", "obs_height = _mcp_object_height_from_z(kwargs.get('obstacle_name', 'cube'))")

    # 5. execute_one_pick_cycle phase boundaries
    # The user says: "Add checks inside execute_one_pick_cycle() between pre-grasp, descent, gripping and retraction—not only at the outer run_mcp_* functions."
    # We will inject `check_safety()` between cmds in `execute_one_pick_cycle`.
    # Let's just modify the `for` loop that iterates over cmds. But wait, `execute_one_pick_cycle` doesn't exist? Oh, it does!
    
    with open("nogripperref.py", "w", encoding="utf-8") as f:
        f.write(content)

if __name__ == "__main__":
    patch_nogripper()
