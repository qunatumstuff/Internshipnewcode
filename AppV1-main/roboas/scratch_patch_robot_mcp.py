import re

def patch_robot_mcp():
    with open("robot_mcp.py", "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Update token handling
    old_token = """SAFETY_TOKEN = os.environ.get("SAFETY_TOKEN", "default-secure-token-xyz")"""
    new_token = """SAFETY_CLEAR_TOKEN = os.environ.get("SAFETY_CLEAR_TOKEN")
if not SAFETY_CLEAR_TOKEN:
    raise ValueError("CRITICAL: SAFETY_CLEAR_TOKEN is missing from the environment.")"""
    content = content.replace(old_token, new_token)
    
    # Update token checks
    content = content.replace("if args.get(\"token\") != SAFETY_TOKEN:", "if args.get(\"token\") != SAFETY_CLEAR_TOKEN:")

    # 2. Check for emergency stop and clear_emergency_stop logic
    # Is clear_emergency_stop properly gated with manual_confirmed and the lock?
    # Let's inspect it after we do a basic replace.
    with open("robot_mcp.py", "w", encoding="utf-8") as f:
        f.write(content)

if __name__ == "__main__":
    patch_robot_mcp()
