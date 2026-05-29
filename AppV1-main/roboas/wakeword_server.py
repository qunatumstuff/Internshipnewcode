import asyncio
import websockets
import json
import vosk
import queue
import threading
import sounddevice as sd
import sys

# ─────────────────────────────────────────────────────────
# Vosk model init
# ─────────────────────────────────────────────────────────
print("Loading Vosk Offline Model...")
try:
    model = vosk.Model(lang="en-us")
    print("Vosk Model loaded successfully!")
except Exception as e:
    print(f"Error loading Vosk model: {e}")
    sys.exit(1)

# ─────────────────────────────────────────────────────────
# Shared state
# ─────────────────────────────────────────────────────────
active_persona  = "john"
is_muted        = False
connected_clients = set()
audio_queue     = queue.Queue()

# Event: set True to tell the audio thread to stop the mic
mic_stop_event  = threading.Event()
# Event: set True to tell the audio thread to restart the mic
mic_start_event = threading.Event()
mic_start_event.set()  # start in listening state


# ─────────────────────────────────────────────────────────
# Audio callback (feeds raw PCM into the queue)
# ─────────────────────────────────────────────────────────
def audio_callback(indata, frames, time, status):
    if status:
        print(f"Sounddevice status: {status}", file=sys.stderr)
    # Only enqueue if we are supposed to be listening
    if not mic_stop_event.is_set():
        audio_queue.put(bytes(indata))


# ─────────────────────────────────────────────────────────
# Broadcast wake word to all Flutter clients
# ─────────────────────────────────────────────────────────
async def broadcast_wakeword(text, persona):
    if not connected_clients:
        print(f"[PY WAKE] heard '{text}' but no Flutter clients connected.")
        return

    event_msg = json.dumps({
        "event": "WAKE_WORD_DETECTED",
        "model": text,
        "persona": persona
    })
    results = await asyncio.gather(
        *[client.send(event_msg) for client in connected_clients],
        return_exceptions=True
    )
    for r in results:
        if isinstance(r, Exception):
            print(f"[PY WAKE] send error: {r}")
    print("[PY WAKE] event sent to Flutter")


# ─────────────────────────────────────────────────────────
# Vosk worker – owns the microphone stream
# The stream is opened, closed, and re-opened here
# entirely driven by mic_stop_event / mic_start_event.
# ─────────────────────────────────────────────────────────
def vosk_worker(loop):
    global active_persona

    grammar = '["hey john", "john", "hey linda", "linda", "lind", "hey lind", "[unk]"]'

    while True:
        # Wait until we are allowed to open the mic
        mic_start_event.wait()
        mic_start_event.clear()
        mic_stop_event.clear()

        print("[PY WAKE] mic stream opening...")
        rec = vosk.KaldiRecognizer(model, 16000, grammar)

        try:
            with sd.RawInputStream(
                samplerate=16000,
                blocksize=4000,
                dtype='int16',
                channels=1,
                callback=audio_callback,
                device=None          # use default (first available) device
            ):
                print("[PY WAKE] mic stream reopened – listening for wake word...")

                while not mic_stop_event.is_set():
                    try:
                        data = audio_queue.get(timeout=0.5)
                    except queue.Empty:
                        continue

                    if rec.AcceptWaveform(data):
                        res  = json.loads(rec.Result())
                        text = res.get('text', '')
                        if text:
                            print(f"[PY VOSK RAW] RESULT: {res}")
                    else:
                        res  = json.loads(rec.PartialResult())
                        text = res.get('partial', '')
                        if text:
                            print(f"[PY VOSK RAW] PARTIAL: {res}")

                    if text and text != "[unk]":
                        print(f"[PY WAKE RECOGNIZED] text='{text}'")
                        current_persona_local = active_persona.lower()
                        is_correct = (
                            (current_persona_local == "john" and "john" in text) or
                            (current_persona_local == "linda" and ("linda" in text or "lind" in text))
                        )

                        if is_correct:
                            if is_muted:
                                print(f"[PY WAKE] detected but MUTED (TTS active): {text}")
                                rec.Reset()
                            else:
                                print(f"[PY WAKE] detected: {text}")

                                # ── YIELD THE MIC ──────────────────────
                                mic_stop_event.set()   # signal: stop mic

                                # Flush the queue so stale audio isn't processed
                                while not audio_queue.empty():
                                    try:
                                        audio_queue.get_nowait()
                                    except queue.Empty:
                                        break

                                # Broadcast AFTER setting stop so the with-block exits
                                asyncio.run_coroutine_threadsafe(
                                    broadcast_wakeword(text, current_persona_local),
                                    loop
                                )
                                rec.Reset()

        except Exception as e:
            print(f"[PY WAKE] mic stream error: {e}", file=sys.stderr)

        print("[PY WAKE] mic stream closed – waiting for restart signal...")


# ─────────────────────────────────────────────────────────
# WebSocket client handler
# ─────────────────────────────────────────────────────────
async def handle_client(websocket):
    global active_persona, is_muted
    connected_clients.add(websocket)
    print(f"[PY WAKE] Flutter client connected. Total: {len(connected_clients)}")

    # Send current persona so Flutter is in sync
    try:
        await websocket.send(json.dumps({
            "event": "PERSONA_SYNC",
            "persona": active_persona
        }))
    except Exception as e:
        print(f"[PY WAKE] Failed to send initial persona sync: {e}")

    try:
        async for message in websocket:
            if isinstance(message, str):
                try:
                    data = json.loads(message)
                    action = data.get("action", "")

                    if action == "set_persona":
                        active_persona = data.get("persona", "john").lower()
                        print(f"[PY WAKE] persona set to: {active_persona}")

                    elif action == "mute":
                        is_muted = True
                        print("[PY WAKE] MUTED (TTS playing)")

                    elif action == "unmute":
                        is_muted = False
                        print("[PY WAKE] UNMUTED (TTS finished)")

                    elif action == "pause_wakeword":
                        # Flutter is about to open the browser mic – release ours
                        print("[PY WAKE] received pause_wakeword from Flutter – releasing mic")
                        mic_stop_event.set()
                        while not audio_queue.empty():
                            try:
                                audio_queue.get_nowait()
                            except queue.Empty:
                                break
                        print("[PY WAKE] mic stream closed")

                    elif action in ("resume_wakeword", "restart_wakeword"):
                        # Flutter finished recording – give mic back to Python
                        print(f"[PY WAKE] received {action} from Flutter")
                        mic_stop_event.clear()
                        mic_start_event.set()
                        print("[PY WAKE] mic stream reopened")

                except Exception as e:
                    print(f"[PY WAKE] Error parsing message: {e}")

    except websockets.exceptions.ConnectionClosed:
        print("[PY WAKE] client disconnected (connection closed).")
    except Exception as e:
        print(f"[PY WAKE] WebSocket error: {e}")
    finally:
        connected_clients.discard(websocket)
        print(f"[PY WAKE] client removed. Total: {len(connected_clients)}")


# ─────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────
async def main():
    loop = asyncio.get_running_loop()
    threading.Thread(target=vosk_worker, args=(loop,), daemon=True).start()
    print("Starting Vosk Wake Word Server on ws://0.0.0.0:8003")
    async with websockets.serve(handle_client, "0.0.0.0", 8003):
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())
