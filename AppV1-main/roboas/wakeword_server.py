import asyncio
import websockets
import json
import vosk
import queue
import threading
import sounddevice as sd
import sys
import os

# ─────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────
SAMPLE_RATE     = 16000
BLOCK_SIZE      = 4000      # ~250ms per block
CHANNELS        = 1
DTYPE           = 'int16'
MIC_DEVICE      = "hw:2,0"  # Default ALSA device on Raspberry Pi (adjustable in OS if needed)

print("[PY] Loading Vosk Offline Model...")
try:
    model = vosk.Model(lang="en-us")
    print("[PY] Vosk Model loaded successfully!")
except Exception as e:
    print(f"[PY] Error loading Vosk model: {e}")
    sys.exit(1)

# ─────────────────────────────────────────────────────────
# Shared state
# ─────────────────────────────────────────────────────────
active_persona    = "john"
is_muted          = False
connected_clients = set()
audio_queue       = queue.Queue()

# Threading events for mic control
mic_stop_event    = threading.Event()
mic_start_event   = threading.Event()

# The asyncio loop reference (set in main)
main_loop = None


# ─────────────────────────────────────────────────────────
# Audio callback (feeds raw PCM into the queue)
# ─────────────────────────────────────────────────────────
def audio_callback(indata, frames, time_info, status):
    if status:
        print(f"[PY] Sounddevice status: {status}", file=sys.stderr)
    if not mic_stop_event.is_set():
        audio_queue.put(bytes(indata))


# ─────────────────────────────────────────────────────────
# Broadcast events to all Flutter clients
# ─────────────────────────────────────────────────────────
async def broadcast_event(event_name, payload=None):
    if not connected_clients:
        return

    msg_dict = {"event": event_name}
    if payload:
        msg_dict.update(payload)

    event_msg = json.dumps(msg_dict)
    results = await asyncio.gather(
        *[client.send(event_msg) for client in connected_clients],
        return_exceptions=True
    )
    for r in results:
        if isinstance(r, Exception):
            print(f"[PY] Send error: {r}")
    print(f"[PY] Sent {event_name}")


def broadcast(event_name, payload=None):
    """Thread-safe wrapper to broadcast from non-async code."""
    if main_loop:
        asyncio.run_coroutine_threadsafe(
            broadcast_event(event_name, payload), main_loop
        )


# ─────────────────────────────────────────────────────────
# Vosk worker – runs in a background thread
# Opens and releases the mic stream dynamically
# ─────────────────────────────────────────────────────────
def vosk_worker(loop):
    global active_persona, main_loop
    main_loop = loop

    grammar = '["hey john", "john", "hey linda", "linda", "lind", "hey lind", "[unk]"]'

    while True:
        # Wait until we are told to open the mic (start_wakeword)
        mic_start_event.wait()
        mic_start_event.clear()
        mic_stop_event.clear()

        # Flush any stale audio from the queue
        while not audio_queue.empty():
            try:
                audio_queue.get_nowait()
            except queue.Empty:
                break

        print("[PY] Opening mic stream for wake-word listening...")
        rec = vosk.KaldiRecognizer(model, SAMPLE_RATE, grammar)
        broadcast("WAKEWORD_STARTED")

        try:
            # Open mic stream (released automatically when block is exited)
            with sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                dtype=DTYPE,
                channels=CHANNELS,
                callback=audio_callback,
                device=MIC_DEVICE
            ):
                print("[PY] Microphone open, listening for wake word...")
                while not mic_stop_event.is_set():
                    try:
                        data = audio_queue.get(timeout=0.5)
                    except queue.Empty:
                        continue

                    if rec.AcceptWaveform(data):
                        res = json.loads(rec.Result())
                        text = res.get('text', '')
                    else:
                        res = json.loads(rec.PartialResult())
                        text = res.get('partial', '')

                    if text and text != "[unk]":
                        print(f"[PY] Heard speech: '{text}'")
                        current_persona_local = active_persona.lower()
                        is_match = (
                            (current_persona_local == "john" and "john" in text) or
                            (current_persona_local == "linda" and ("linda" in text or "lind" in text))
                        )

                        if is_match:
                            if is_muted:
                                print(f"[PY] Wake word detected but MUTED: {text}")
                                rec.Reset()
                            else:
                                print(f"[PY] WAKE WORD DETECTED: {text}")
                                # Broadcast event to Flutter
                                broadcast("WAKE_WORD_DETECTED", {
                                    "model": text,
                                    "persona": current_persona_local
                                })
                                # Stop mic immediately so Flutter browser mic can take over
                                mic_stop_event.set()
                                break
        except Exception as e:
            print(f"[PY] Microphone stream error: {e}", file=sys.stderr)

        print("[PY] Microphone released")
        broadcast("WAKEWORD_STOPPED")


# ─────────────────────────────────────────────────────────
# WebSocket client handler
# ─────────────────────────────────────────────────────────
async def handle_client(websocket):
    global active_persona, is_muted
    connected_clients.add(websocket)
    print(f"[PY] Client connected. Total: {len(connected_clients)}")

    # Send server ready + persona sync
    try:
        await websocket.send(json.dumps({"event": "AUDIO_SERVER_READY"}))
        await websocket.send(json.dumps({
            "event": "PERSONA_SYNC",
            "persona": active_persona
        }))
    except Exception as e:
        print(f"[PY] Failed to send initial sync: {e}")

    try:
        async for message in websocket:
            if isinstance(message, str):
                try:
                    data = json.loads(message)
                    action = data.get("action", "")

                    if action == "set_persona":
                        active_persona = data.get("persona", "john").lower()
                        print(f"[PY] Persona set to: {active_persona}")

                    elif action == "mute":
                        is_muted = True
                        print("[PY] Wake-word muted (TTS speaking)")

                    elif action == "unmute":
                        is_muted = False
                        print("[PY] Wake-word unmuted (TTS finished)")

                    elif action in ("start_wakeword", "resume_wakeword", "restart_wakeword"):
                        print(f"[PY] Received '{action}' – opening mic")
                        mic_stop_event.clear()
                        mic_start_event.set()

                    elif action in ("stop_wakeword", "pause_wakeword"):
                        print(f"[PY] Received '{action}' – releasing mic")
                        mic_stop_event.set()

                except Exception as e:
                    print(f"[PY] Error parsing message: {e}")

    except websockets.exceptions.ConnectionClosed:
        print("[PY] Client disconnected.")
    finally:
        connected_clients.discard(websocket)
        print(f"[PY] Client removed. Total: {len(connected_clients)}")


# ─────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────
async def main():
    loop = asyncio.get_running_loop()
    # Start the background Vosk listening thread
    threading.Thread(target=vosk_worker, args=(loop,), daemon=True).start()
    print("[PY] Wakeword Server starting on ws://0.0.0.0:8003")
    async with websockets.serve(handle_client, "0.0.0.0", 8003):
        print("[PY] Wakeword Server ready")
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())
