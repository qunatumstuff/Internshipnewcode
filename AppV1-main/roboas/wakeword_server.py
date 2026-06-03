import asyncio
import websockets
import json
import vosk
import queue
import threading
import sounddevice as sd
import sys
import struct
import math
import os
import io
import wave

# ── HTTP upload ──
try:
    import requests
except ImportError:
    print("[PY] 'requests' not installed. Run: pip install requests")
    sys.exit(1)

# ─────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────
NODE_SERVER_URL = os.environ.get("NODE_SERVER_URL", "http://172.22.34.95:3000")
SAMPLE_RATE     = 16000
BLOCK_SIZE      = 4000      # ~250ms per block
CHANNELS        = 1
DTYPE           = 'int16'
MIC_DEVICE      = "hw:2,0"  # ALSA device on Raspberry Pi

# Silence detection thresholds (same as Flutter VAD)
SPEECH_THRESHOLD   = 0.02
SILENCE_THRESHOLD  = 0.008
SILENCE_TICKS      = 15     # 1.5s silence after speech → stop
INITIAL_SILENCE    = 40     # 4s no speech → stop
MAX_TICKS          = 100    # 10s max recording duration

# ─────────────────────────────────────────────────────────
# Vosk model init
# ─────────────────────────────────────────────────────────
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

# Recording state — shared between threads
record_request    = threading.Event()   # Flutter asked to record
stop_record_req   = threading.Event()   # Flutter asked to stop recording early
is_recording      = threading.Event()   # True while actively recording user speech

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
        print(f"[PY] '{event_name}' but no clients connected.")
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
            print(f"[PY] send error: {r}")
    print(f"[PY] sent {event_name}")


def broadcast(event_name, payload=None):
    """Thread-safe wrapper to broadcast from non-async code."""
    if main_loop:
        asyncio.run_coroutine_threadsafe(
            broadcast_event(event_name, payload), main_loop
        )


# ─────────────────────────────────────────────────────────
# RMS calculation for silence detection
# ─────────────────────────────────────────────────────────
def calculate_rms(pcm_bytes):
    """Calculate RMS amplitude from raw 16-bit PCM bytes. Returns 0.0 to 1.0."""
    n_samples = len(pcm_bytes) // 2
    if n_samples == 0:
        return 0.0
    fmt = f"<{n_samples}h"  # little-endian signed 16-bit
    samples = struct.unpack(fmt, pcm_bytes)
    sum_sq = sum(s * s for s in samples)
    rms = math.sqrt(sum_sq / n_samples)
    return rms / 32768.0  # normalize to 0.0-1.0


# ─────────────────────────────────────────────────────────
# Build WAV file from raw PCM chunks
# ─────────────────────────────────────────────────────────
def build_wav(pcm_chunks):
    """Convert list of raw PCM byte chunks into a WAV file in memory."""
    raw_data = b"".join(pcm_chunks)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # 16-bit = 2 bytes
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(raw_data)
    buf.seek(0)
    return buf


# ─────────────────────────────────────────────────────────
# Upload audio to Node /transcribe and return text
# ─────────────────────────────────────────────────────────
def upload_and_transcribe(wav_buf):
    """POST the WAV audio to Node's /transcribe endpoint. Returns transcript text or None."""
    url = f"{NODE_SERVER_URL}/transcribe"
    print(f"[PY] uploading audio to {url}")
    try:
        files = {
            'audio': ('recording.wav', wav_buf, 'audio/wav')
        }
        resp = requests.post(url, files=files, timeout=30)
        print(f"[PY] /transcribe response status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success") and data.get("text", "").strip():
                text = data["text"].strip()
                print(f'[PY] transcription received: "{text}"')
                return text
            else:
                print("[PY] transcription returned empty text")
                return None
        else:
            print(f"[PY] /transcribe error: HTTP {resp.status_code}")
            return None
    except Exception as e:
        print(f"[PY] /transcribe upload error: {e}")
        return None


# ─────────────────────────────────────────────────────────
# Record user speech with silence detection
# ─────────────────────────────────────────────────────────
def record_user_speech():
    """
    Record audio from the mic until silence is detected or timeout.
    Returns list of raw PCM chunks, or empty list if aborted.
    """
    pcm_chunks = []
    has_spoken = False
    silence_ticks = 0
    elapsed_ticks = 0

    print("[PY] recording started")
    broadcast("RECORDING_STARTED")
    is_recording.set()
    stop_record_req.clear()

    # Flush any stale audio from the queue
    while not audio_queue.empty():
        try:
            audio_queue.get_nowait()
        except queue.Empty:
            break

    while not mic_stop_event.is_set() and not stop_record_req.is_set():
        try:
            data = audio_queue.get(timeout=0.1)
        except queue.Empty:
            elapsed_ticks += 1
            if elapsed_ticks >= MAX_TICKS:
                print("[PY] max recording duration reached (10s)")
                break
            continue

        pcm_chunks.append(data)
        elapsed_ticks += 1

        if elapsed_ticks >= MAX_TICKS:
            print("[PY] max recording duration reached (10s)")
            break

        rms = calculate_rms(data)

        if rms > SPEECH_THRESHOLD:
            if not has_spoken:
                has_spoken = True
                print(f"[PY] speech detected (RMS: {rms:.4f})")
            silence_ticks = 0
        elif rms < SILENCE_THRESHOLD:
            if has_spoken:
                silence_ticks += 1
                if silence_ticks >= SILENCE_TICKS:
                    print("[PY] silence detected")
                    break
            else:
                if elapsed_ticks >= INITIAL_SILENCE:
                    print("[PY] no speech detected for 4s")
                    break
        else:
            silence_ticks = 0

    is_recording.clear()
    print("[PY] recording stopped")
    broadcast("RECORDING_STOPPED")
    return pcm_chunks


# ─────────────────────────────────────────────────────────
# Full recording → transcription → send result pipeline
# ─────────────────────────────────────────────────────────
def recording_pipeline():
    """Record → build WAV → upload → broadcast STT_RESULT."""
    pcm_chunks = record_user_speech()

    if not pcm_chunks:
        print("[PY] no audio captured, aborting pipeline")
        broadcast("STT_ERROR", {"error": "No audio captured"})
        return

    # Build WAV from PCM
    wav_buf = build_wav(pcm_chunks)
    wav_size = wav_buf.getbuffer().nbytes
    print(f"[PY] WAV file size: {wav_size} bytes")

    if wav_size < 5000:
        print("[PY] audio too short, aborting")
        broadcast("STT_ERROR", {"error": "Audio too short"})
        return

    # Upload and transcribe
    broadcast("TRANSCRIBING")
    text = upload_and_transcribe(wav_buf)

    if text:
        print(f"[PY] transcript sent to Flutter")
        broadcast("STT_RESULT", {"text": text})
    else:
        broadcast("STT_ERROR", {"error": "Transcription failed or empty"})


# ─────────────────────────────────────────────────────────
# Vosk worker – owns the microphone stream
# Handles both wakeword and recording in a single thread
# ─────────────────────────────────────────────────────────
def vosk_worker(loop):
    global active_persona, main_loop
    main_loop = loop

    grammar = '["hey john", "john", "hey linda", "linda", "lind", "hey lind", "[unk]"]'

    while True:
        # Wait until we are told to open the mic
        # (for wakeword listening or a record_now request)
        mic_start_event.wait()
        mic_start_event.clear()
        mic_stop_event.clear()

        # Check if this was a record_now request (manual mic)
        if record_request.is_set():
            record_request.clear()
            print("[PY] manual record_now – opening mic for recording")
            try:
                with sd.RawInputStream(
                    samplerate=SAMPLE_RATE,
                    blocksize=BLOCK_SIZE,
                    dtype=DTYPE,
                    channels=CHANNELS,
                    callback=audio_callback,
                    device=MIC_DEVICE
                ):
                    recording_pipeline()
            except Exception as e:
                print(f"[PY] mic error during manual recording: {e}", file=sys.stderr)
                broadcast("STT_ERROR", {"error": str(e)})

            print("[PY] mic released (manual recording done)")
            broadcast("WAKEWORD_STOPPED")
            continue

        # ── Normal wakeword listening ─────────────────────
        print("[PY] reopening mic stream for wakeword...")
        rec = vosk.KaldiRecognizer(model, SAMPLE_RATE, grammar)

        try:
            with sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                dtype=DTYPE,
                channels=CHANNELS,
                callback=audio_callback,
                device=MIC_DEVICE
            ):
                print("[PY] wakeword listening started")
                broadcast("WAKEWORD_STARTED")

                while not mic_stop_event.is_set():
                    # Check for mid-listen record_now request
                    if record_request.is_set():
                        record_request.clear()
                        print("[PY] record_now during wakeword listening")
                        broadcast("WAKE_WORD_DETECTED", {"model": "manual", "persona": active_persona})
                        recording_pipeline()
                        # After recording, exit the wakeword loop
                        # Flutter will send start_wakeword again if Hands Off is still ON
                        mic_stop_event.set()
                        break

                    text = ''
                    try:
                        data = audio_queue.get(timeout=0.5)
                    except queue.Empty:
                        continue

                    if rec.AcceptWaveform(data):
                        res = json.loads(rec.Result())
                        text = res.get('text', '')
                        if text:
                            print(f"[PY VOSK] RESULT: {res}")
                    else:
                        res = json.loads(rec.PartialResult())
                        text = res.get('partial', '')
                        if text:
                            print(f"[PY VOSK] PARTIAL: {res}")

                    if text and text != "[unk]":
                        print(f"[PY WAKE] recognized: '{text}'")
                        current_persona = active_persona.lower()
                        is_correct = (
                            (current_persona == "john" and "john" in text) or
                            (current_persona == "linda" and ("linda" in text or "lind" in text))
                        )

                        if is_correct:
                            if is_muted:
                                print(f"[PY] wakeword detected but MUTED: {text}")
                                rec.Reset()
                            else:
                                print("[PY] wakeword detected")

                                # Flush stale audio
                                while not audio_queue.empty():
                                    try:
                                        audio_queue.get_nowait()
                                    except queue.Empty:
                                        break

                                broadcast("WAKE_WORD_DETECTED", {
                                    "model": text,
                                    "persona": current_persona
                                })

                                # Continue to record user speech (Python keeps mic)
                                recording_pipeline()

                                # After recording, exit loop
                                # Flutter sends start_wakeword after TTS
                                mic_stop_event.set()
                                rec.Reset()
                                break

        except Exception as e:
            print(f"[PY] mic stream error: {e}", file=sys.stderr)

        print("[PY] mic released")
        broadcast("WAKEWORD_STOPPED")


# ─────────────────────────────────────────────────────────
# WebSocket client handler
# ─────────────────────────────────────────────────────────
async def handle_client(websocket):
    global active_persona, is_muted
    connected_clients.add(websocket)
    print(f"[PY] client connected. Total: {len(connected_clients)}")

    # Send server ready + persona sync
    try:
        await websocket.send(json.dumps({"event": "AUDIO_SERVER_READY"}))
        await websocket.send(json.dumps({
            "event": "PERSONA_SYNC",
            "persona": active_persona
        }))
    except Exception as e:
        print(f"[PY] Failed to send initial events: {e}")

    try:
        async for message in websocket:
            if isinstance(message, str):
                try:
                    data = json.loads(message)
                    action = data.get("action", "")

                    if action == "set_persona":
                        active_persona = data.get("persona", "john").lower()
                        print(f"[PY] persona set to: {active_persona}")

                    elif action == "mute":
                        is_muted = True
                        print("[PY] MUTED (TTS playing)")

                    elif action == "unmute":
                        is_muted = False
                        print("[PY] UNMUTED (TTS finished)")

                    elif action == "record_now":
                        print("[PY] received record_now from Flutter")
                        # If wakeword is listening, interrupt it
                        record_request.set()
                        # If mic is not currently open, also trigger mic_start
                        if not mic_start_event.is_set():
                            mic_start_event.set()

                    elif action == "stop_recording":
                        print("[PY] received stop_recording from Flutter")
                        stop_record_req.set()

                    elif action in ("pause_wakeword", "stop_wakeword"):
                        print(f"[PY] received {action} – releasing mic")
                        mic_stop_event.set()
                        while not audio_queue.empty():
                            try:
                                audio_queue.get_nowait()
                            except queue.Empty:
                                break

                    elif action in ("resume_wakeword", "restart_wakeword", "start_wakeword"):
                        print(f"[PY] received {action}")
                        record_request.clear()
                        mic_stop_event.clear()
                        mic_start_event.set()

                except Exception as e:
                    print(f"[PY] Error parsing message: {e}")

    except websockets.exceptions.ConnectionClosed:
        print("[PY] client disconnected.")
    except Exception as e:
        print(f"[PY] WebSocket error: {e}")
    finally:
        connected_clients.discard(websocket)
        print(f"[PY] client removed. Total: {len(connected_clients)}")


# ─────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────
async def main():
    loop = asyncio.get_running_loop()
    # Do NOT auto-start wakeword — wait for Flutter to send start_wakeword
    threading.Thread(target=vosk_worker, args=(loop,), daemon=True).start()
    print(f"[PY] Audio Server starting on ws://0.0.0.0:8003")
    print(f"[PY] NODE_SERVER_URL = {NODE_SERVER_URL}")
    async with websockets.serve(handle_client, "0.0.0.0", 8003):
        print("[PY] Audio Server ready")
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())
