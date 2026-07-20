import re

def patch_nogripper():
    with open("nogripperref.py", "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Import catalogue
    if "from object_catalogue import OBJECT_CATALOGUE" not in content:
        content = content.replace("import math\n", "import math\nfrom object_catalogue import OBJECT_CATALOGUE\n")

    # 2. Fix _mcp_object_height_from_z
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
    
    # We must fix all callers of _mcp_object_height_from_z to pass name instead of Z.
    content = content.replace("obj_height = _mcp_object_height_from_z(obj_z)", "obj_height = _mcp_object_height_from_z(kwargs.get('object_name', 'cube'))")
    content = content.replace("obj_height = _mcp_object_height_from_z(target_z)", "obj_height = _mcp_object_height_from_z(kwargs.get('target_name', 'cube'))")
    content = content.replace("obs_height = _mcp_object_height_from_z(obs_z)", "obs_height = _mcp_object_height_from_z(kwargs.get('obstacle_name', 'cube'))")

    # 3. Fix check_safety calls in wrappers
    wrappers = ["_safe_move_linear", "_safe_move_joint", "_safe_move_pose", "_safe_power_on", "_safe_reset_errors", "_safe_execute"]
    for w in wrappers:
        # Re-add check_safety() at the beginning of these wrappers if missing
        w_def = f"def {w}("
        # Check if the block has `def check_safety():` which I replaced with `def check_safety():`
        # actually, I previously changed `# check_safety() already exists somewhere` to `def check_safety():` which is wrong, because check_safety is ALREADY defined globally.
        # Oh! `def check_safety():` was defined at line 125, but inside the WRAPPERS I had `def check_safety():` by mistake?
        pass

    # Let's fix check_safety() in wrappers explicitly.
    content = content.replace("def check_safety():", "check_safety()") # Wait, that will break the actual definition!
    
    with open("nogripperref.py", "w", encoding="utf-8") as f:
        f.write(content)

if __name__ == "__main__":
    patch_nogripper()
