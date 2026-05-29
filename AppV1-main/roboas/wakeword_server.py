import asyncio
import websockets
import json
import vosk
import queue
import threading
import sounddevice as sd
import sys
import time
import requests
import wave
import math
import struct
from enum import Enum

class State(Enum):
    IDLE = 1
    RECORDING = 2
    TRANSCRIBING = 3
    THINKING = 4
    SPEAKING = 5

# Global State
state = State.IDLE
active_persona = "john"
connected_clients = set()
audio_queue = queue.Queue()
backend_url = None
audio_buffer = bytearray()
last_wake_time = 0

# Initialize Vosk
print("Loading Vosk Offline Model...")
try:
    model = vosk.Model(lang="en-us")
    print("Vosk Model loaded successfully!")
except Exception as e:
    print(f"Error loading Vosk model: {e}")
    sys.exit(1)

def audio_callback(indata, frames, time_info, status):
    """Called continuously by sounddevice for each block"""
    if status:
        pass # Ignore minor ALSA underflows to avoid spam
    audio_queue.put(bytes(indata))

def calculate_rms(audio_bytes):
    count = len(audio_bytes) // 2
    if count == 0: return 0
    try:
        shorts = struct.unpack(f"<{count}h", audio_bytes)
        sum_squares = sum((s ** 2 for s in shorts))
        return math.sqrt(sum_squares / count)
    except:
        return 0

def stop_recording_timer(loop, current_persona):
    global state, audio_buffer
    if state == State.RECORDING:
        print("[TIMER] 4-second recording timeout reached.")
        set_state(State.TRANSCRIBING, loop)
        threading.Thread(
            target=transcribe_thread, 
            args=(bytes(audio_buffer), current_persona, loop), 
            daemon=True
        ).start()

def set_state(new_state, loop):
    global state
    if state != new_state:
        print(f"\n[STATE TRANSITION] {state.name} -> {new_state.name}")
        state = new_state
        asyncio.run_coroutine_threadsafe(
            broadcast_event({"event": "STATE_CHANGE", "state": state.name}), loop
        )

async def broadcast_event(event_msg):
    if not connected_clients:
        return
    await asyncio.gather(*[client.send(json.dumps(event_msg)) for client in connected_clients], return_exceptions=True)

def transcribe_thread(audio_bytes, persona, loop):
    global backend_url
    
    # Save debug.wav
    filename = "debug_audio.wav"
    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2) # 16-bit
        wf.setframerate(16000)
        wf.writeframes(audio_bytes)
    
    duration = len(audio_bytes) / (16000 * 2)
    rms = calculate_rms(audio_bytes)
    print(f"[DEBUG] Audio captured | Duration: {duration:.2f}s | RMS: {rms:.2f}")
    
    if rms < 10:
        print("[WARNING] Audio appears to be almost completely silent!")
        
    if not backend_url:
        print("[ERROR] No backendUrl configured from Flutter. Cannot transcribe.")
        set_state(State.IDLE, loop)
        return
        
    print(f"[TRANSCRIBING] Uploading {filename} to {backend_url}/transcribe")
    try:
        files = {'audio': open(filename, 'rb')}
        data = {'persona': persona, 'fromWakeWord': 'true'}
        res = requests.post(f"{backend_url}/transcribe", files=files, data=data, timeout=30)
        res.raise_for_status()
        
        result_json = res.json()
        if result_json.get("success"):
            recognized_text = result_json.get("text", "")
            print(f"[STT RESULT] '{recognized_text}'")
            # Send result to Flutter
            asyncio.run_coroutine_threadsafe(
                broadcast_event({"event": "STT_RESULT", "text": recognized_text}), loop
            )
            # Enter THINKING state until LLM replies and triggers TTS (SPEAKING)
            set_state(State.THINKING, loop)
        else:
            print("[ERROR] Transcription API returned success=false")
            set_state(State.IDLE, loop)
    except Exception as e:
        print(f"[ERROR] Transcribe upload failed: {e}")
        set_state(State.IDLE, loop)

def vosk_worker(loop):
    global active_persona, state, audio_buffer, last_wake_time
    print("Vosk worker thread started!")
    grammar = '["hey john", "john", "hey linda", "linda", "lind", "hey lind", "[unk]"]'
    rec = vosk.KaldiRecognizer(model, 16000, grammar)
    
    try:
        # OPEN THE MICROPHONE ONCE AND NEVER CLOSE IT!
        with sd.RawInputStream(samplerate=16000, blocksize=4000, dtype='int16',
                               channels=1, callback=audio_callback, device=1):
            print("Microphone continuous stream opened successfully on device 1!")
            
            while True:
                data = audio_queue.get()
                
                if state == State.IDLE:
                    if rec.AcceptWaveform(data):
                        res = json.loads(rec.Result())
                        text = res.get('text', '')
                    else:
                        res = json.loads(rec.PartialResult())
                        text = res.get('partial', '')
                    
                    if text != "" and text != "[unk]":
                        current_persona_local = active_persona.lower()
                        is_correct_persona = False
                        
                        if current_persona_local == "john" and "john" in text:
                            is_correct_persona = True
                        elif current_persona_local == "linda" and ("linda" in text or "lind" in text):
                            is_correct_persona = True
                        
                        if is_correct_persona:
                            now = time.time()
                            if now - last_wake_time > 3.0: # 3 sec cooldown
                                print(f"\n*** WAKE WORD DETECTED: {text} ***")
                                last_wake_time = now
                                audio_buffer = bytearray()
                                set_state(State.RECORDING, loop)
                                # Start 4-second auto-stop timer
                                loop.call_later(4.0, stop_recording_timer, loop, current_persona_local)
                                rec.Reset()
                            else:
                                print("[DEBUG] Wake word ignored (cooldown)")
                                rec.Reset()
                                
                elif state == State.RECORDING:
                    # Collect frames for speech
                    audio_buffer.extend(data)
                    
                elif state in [State.TRANSCRIBING, State.THINKING, State.SPEAKING]:
                    # Throw away frames (don't feed Vosk, don't save them)
                    pass
                    
    except Exception as e:
        print(f"FATAL ALSA/Stream Error: {e}", file=sys.stderr)

async def handle_client(websocket):
    global active_persona, state, audio_buffer, backend_url
    connected_clients.add(websocket)
    print(f"Client connected! Total: {len(connected_clients)}")
    
    try:
        await websocket.send(json.dumps({"event": "PERSONA_SYNC", "persona": active_persona}))
    except: pass
    
    try:
        async for message in websocket:
            if isinstance(message, str):
                try:
                    data = json.loads(message)
                    action = data.get("action")
                    
                    if action == "set_base_url":
                        backend_url = data.get("url")
                        print(f"[CONFIG] backendUrl set to: {backend_url}")
                        
                    elif action == "set_persona":
                        active_persona = data.get("persona", "john").lower()
                        print(f"[CONFIG] Active persona: {active_persona}")
                        
                    elif action == "start_recording":
                        if state == State.IDLE:
                            audio_buffer = bytearray()
                            loop = asyncio.get_running_loop()
                            set_state(State.RECORDING, loop)
                            loop.call_later(4.0, stop_recording_timer, loop, active_persona)
                            
                    elif action == "stop_recording":
                        if state == State.RECORDING:
                            # Switch state to TRANSCRIBING and spawn thread
                            set_state(State.TRANSCRIBING, asyncio.get_running_loop())
                            threading.Thread(
                                target=transcribe_thread, 
                                args=(bytes(audio_buffer), active_persona, asyncio.get_running_loop()), 
                                daemon=True
                            ).start()
                            
                    elif action == "mute":
                        set_state(State.SPEAKING, asyncio.get_running_loop())
                        
                    elif action == "unmute":
                        set_state(State.IDLE, asyncio.get_running_loop())
                        
                except Exception as e:
                    print(f"Error parsing message: {e}")
    finally:
        connected_clients.remove(websocket)
        print(f"Client disconnected. Remaining: {len(connected_clients)}")

async def main():
    loop = asyncio.get_running_loop()
    threading.Thread(target=vosk_worker, args=(loop,), daemon=True).start()
    print("Starting Continuous-Stream Wake Word Server on ws://0.0.0.0:8003")
    async with websockets.serve(handle_client, "0.0.0.0", 8003):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
