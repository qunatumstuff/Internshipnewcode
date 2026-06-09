import json
import os

transcript_path = r"C:\Users\Dominic\.gemini\antigravity\brain\6c67177b-db98-4266-8e78-a0c226812ed1\.system_generated\logs\transcript.jsonl"

turns = []
with open(transcript_path, "r", encoding="utf-8") as f:
    for line in f:
        try:
            data = json.loads(line)
            source = data.get("source")
            step_type = data.get("type")
            content = data.get("content", "")
            
            # Clean content for cleaner terminal output
            safe_content = content.encode('ascii', errors='replace').decode('ascii')
            lines = [line.strip() for line in safe_content.split('\n') if line.strip()]
            cleaned_lines = []
            for l in lines:
                if any(x in l for x in ["<ADDITIONAL_METADATA>", "</ADDITIONAL_METADATA>", "<USER_INFORMATION>", "</USER_INFORMATION>", "<USER_SETTINGS_CHANGE>", "</USER_SETTINGS_CHANGE>", "The user changed setting", "The current local time"]):
                    continue
                cleaned_lines.append(l)
            cleaned_content = " | ".join(cleaned_lines)
            if len(cleaned_content) > 160:
                cleaned_content = cleaned_content[:160] + "..."
                
            if step_type in ("USER_INPUT", "PLANNER_RESPONSE") or source == "USER_EXPLICIT":
                turns.append((data.get("step_index"), source, step_type, cleaned_content))
        except Exception as e:
            pass

print(f"Total turns: {len(turns)}")
# Print the last 20 turns
for idx, src, t_type, content in turns[-25:]:
    print(f"Step {idx} | {src} ({t_type}): {content}")
