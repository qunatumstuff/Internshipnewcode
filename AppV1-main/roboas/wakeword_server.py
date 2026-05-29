import asyncio
import websockets
import json
import vosk
import queue
import threading
import sounddevice as sd
import sys

# Initialize the Vosk offline model
# Note: Vosk will automatically download a small (~40MB) English model to ~/.cache/vosk on first run.
print("Loading Vosk Offline Model...")
try:
    model = vosk.Model(lang="en-us")
    print("Vosk Model loaded successfully!")
except Exception as e:
    print(f"Error loading Vosk model: {e}")
    print("Please ensure 'vosk' is installed via 'pip install vosk'.")
    sys.exit(1)

active_persona = "john" # Default is john to match server.js and main UI
is_muted = False  # When True, wake word detections are suppressed (e.g. during TTS)
connected_clients = set()
audio_queue = queue.Queue()

def audio_callback(indata, frames, time, status):
    """This is called for each audio block from sounddevice"""
    if status:
        print(f"Sounddevice status: {status}", file=sys.stderr)
    audio_queue.put(bytes(indata))

async def broadcast_wakeword(text, persona):
    if not connected_clients:
        print(f"Vosk heard '{text}', but no clients are connected to receive the wake-word event.")
        return
    
    print(f"Broadcasting WAKE_WORD_DETECTED to {len(connected_clients)} clients...")
    event_msg = json.dumps({
        "event": "WAKE_WORD_DETECTED",
        "model": text,
        "persona": persona
    })
    # Gather all send operations
    await asyncio.gather(*[client.send(event_msg) for client in connected_clients], return_exceptions=True)
    print(f"[PY WAKE] broadcast sent")

def vosk_worker(loop):
    global active_persona
    print("Vosk worker thread started!")
    grammar = '["hey john", "john", "hey linda", "linda", "lind", "hey lind", "[unk]"]'
    rec = vosk.KaldiRecognizer(model, 16000, grammar)
    
    # Start the sounddevice input stream
    try:
        # 16000 Hz, mono, int16 PCM
        with sd.RawInputStream(samplerate=16000, blocksize=4000, dtype='int16',
                               channels=1, callback=audio_callback, device=1):
            print("Microphone listening stream opened successfully! Listening for wake word...")
            while True:
                data = audio_queue.get()
                if rec.AcceptWaveform(data):
                    res = json.loads(rec.Result())
                    text = res.get('text', '')
                else:
                    res = json.loads(rec.PartialResult())
                    text = res.get('partial', '')
                
                if text != "" and text != "[unk]":
                    is_correct_persona = False
                    current_persona_local = active_persona.lower()
                    
                    if current_persona_local == "john" and "john" in text:
                        is_correct_persona = True
                    elif current_persona_local == "linda" and ("linda" in text or "lind" in text):
                        is_correct_persona = True
                    
                    if is_correct_persona:
                        if is_muted:
                            print(f"*** WAKE WORD DETECTED but MUTED (TTS active): {text} ***")
                            rec.Reset()
                        else:
                            print(f"[PY WAKE] detected {text}")
                            print(f"*** WAKE WORD DETECTED: {text} (Persona: {current_persona_local}) ***")
                            # Schedule broadcast on the main event loop
                            asyncio.run_coroutine_threadsafe(
                                broadcast_wakeword(text, current_persona_local), 
                                loop
                            )
                            # Reset the recognizer so it starts fresh after triggering
                            rec.Reset()
    except Exception as e:
        print(f"Error in Vosk worker or audio stream: {e}", file=sys.stderr)

async def handle_client(websocket):
    global active_persona, is_muted
    connected_clients.add(websocket)
    print(f"Client connected to Wake Word Server! Total: {len(connected_clients)}")
    
    # Send the current persona state to the newly connected client immediately
    try:
        await websocket.send(json.dumps({
            "event": "PERSONA_SYNC",
            "persona": active_persona
        }))
    except Exception as e:
        print(f"Failed to send initial persona sync: {e}")
    
    try:
        async for message in websocket:
            if isinstance(message, str):
                try:
                    data = json.loads(message)
                    if data.get("action") == "set_persona":
                        active_persona = data.get("persona", "john").lower()
                        print(f"Active persona changed to: {active_persona}")
                    elif data.get("action") == "mute":
                        is_muted = True
                        print("[MUTE] Wake word detection MUTED (TTS speaking)")
                    elif data.get("action") == "unmute":
                        is_muted = False
                        print("[UNMUTE] Wake word detection UNMUTED (TTS finished)")
                except Exception as e:
                    print(f"Error parsing text message: {e}")
    except websockets.exceptions.ConnectionClosed:
        print("Client disconnected.")
    except Exception as e:
        print(f"WebSocket Error: {e}")
    finally:
        connected_clients.remove(websocket)
        print(f"Client disconnected. Total remaining: {len(connected_clients)}")

async def main():
    loop = asyncio.get_running_loop()
    
    # Start the Vosk background worker thread
    threading.Thread(target=vosk_worker, args=(loop,), daemon=True).start()
    
    print("Starting Vosk Wake Word Server on ws://0.0.0.0:8003")
    async with websockets.serve(handle_client, "0.0.0.0", 8003):
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())
