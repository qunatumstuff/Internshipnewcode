# Baseline Reference Checkpoint (Pre-LoRA Integration)

This checkpoint documents and preserves the stable, fully-functional baseline version of the Roboas application before starting the Parameter-Efficient Fine-Tuning (PEFT) LoRA integration phase.

---

## Preserved Baseline Files
We have copied the exact working source files of this release directly into the artifacts directory for safe keeping and direct referencing:

1. **[baseline_chatbot_screen.dart](file:///C:/Users/Dominic/.gemini/antigravity/brain/6c67177b-db98-4266-8e78-a0c226812ed1/baseline_chatbot_screen.dart)**:
   - Contains the complete Flutter client UI layout (including the E-STOP and HOME buttons).
   - Manages the client-side state machine (`HandsOffState`), Web Speech Fallback TTS, and VAD silence detection.
   - Implements the new **Debugging HUD Overlay & Toggle Button** (`_showDebugPanel` and `_micDebugLogs`).
   - Implements the **2-second cooldown delays** protecting both John and Linda wake word activations from false triggers.

2. **[baseline_server.js](file:///C:/Users/Dominic/.gemini/antigravity/brain/6c67177b-db98-4266-8e78-a0c226812ed1/baseline_server.js)**:
   - Node.js backend managing API routing (transcription uploads, GPT prompts, TTS generation).
   - Integrates Emoji, Vision, and Robot MCP servers.
   - Manages the `/progress` SSE status channel for broadcasting coordinates and moving statuses.
   - Features the persistent Claude Desktop debugging logs (`TOOL_LOG_FILE`).

3. **[baseline_index.html](file:///C:/Users/Dominic/.gemini/antigravity/brain/6c67177b-db98-4266-8e78-a0c226812ed1/baseline_index.html)**:
   - Client web runner that registers Dart interop callbacks and configures the `WakeWordEngine`.
   - Listens to the `/progress` EventSource stream to mute/unmute the wake word on status changes.

4. **[baseline_WakeWordEngine.js](file:///C:/Users/Dominic/.gemini/antigravity/brain/6c67177b-db98-4266-8e78-a0c226812ed1/baseline_WakeWordEngine.js)**:
   - Core JS class processing microphone audio chunks against ONNX models (John, John V2, Linda, Linda V2).

5. **[baseline_robot_mcp.py](file:///C:/Users/Dominic/.gemini/antigravity/brain/6c67177b-db98-4266-8e78-a0c226812ed1/baseline_robot_mcp.py)**:
   - Robot control MCP server exposed via FastMCP. Manages standard robotic arm actions (pick-and-place, relocate, homing).

6. **[baseline_vision_mcp.py](file:///C:/Users/Dominic/.gemini/antigravity/brain/6c67177b-db98-4266-8e78-a0c226812ed1/baseline_vision_mcp.py)**:
   - Vision and camera capture MCP server utilizing depth camera pipelines.

7. **[baseline_mcp_emoji_server.py](file:///C:/Users/Dominic/.gemini/antigravity/brain/6c67177b-db98-4266-8e78-a0c226812ed1/baseline_mcp_emoji_server.py)**:
   - FastMCP server managing active status emojis for the chatbot persona animation states.

8. **[baseline_mcp_debugger_server.py](file:///C:/Users/Dominic/.gemini/antigravity/brain/6c67177b-db98-4266-8e78-a0c226812ed1/baseline_mcp_debugger_server.py)**:
   - Debug and logging monitor MCP server which reads log states from `gpt_tool_log.json` to stream tool metrics.

9. **[project_architecture.md](file:///c:/Users/Dominic/Downloads/AppV1-main/AiChatbot/AppV1-main/project_architecture.md)** (Saved in Project Root):
   - Detailed System Architecture & secure Ngrok tunnel justification report describing the network layout and distributed components.

---

## Current Baseline Functionality (All Working)
- **Personas**: John (male) and Linda (female) switcher is fully functional with respective voices and video state machines.
- **Hands-Free listening**: Stable openWakeWord WebAssembly processing with manual threshold config.
- **Microphone Cooldowns**: Fully protected against robot motor noises and speaker feedback loop triggers.
- **HUD Diagnostics**: Monitored via the on-screen terminal overlays and backend log outputs.

This checkpoint guarantees we can rebuild, revert to, or reference this exact state at any time during the subsequent LoRA integration steps.
