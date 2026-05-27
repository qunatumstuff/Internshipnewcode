import vosk
import queue
import sounddevice as sd
import sys
import json

print("Loading Vosk model...")
model = vosk.Model(lang="en-us")
rec = vosk.KaldiRecognizer(model, 16000)
q = queue.Queue()

def callback(indata, frames, time, status):
    if status:
        print(status, file=sys.stderr)
    q.put(bytes(indata))

print("\n" + "="*50)
print("=== MICROPHONE TEST ACTIVE ===")
print("Please say 'John' or 'Linda' out loud into your microphone...")
print("="*50 + "\n")

try:
    with sd.RawInputStream(samplerate=16000, blocksize=4000, dtype='int16', channels=1, callback=callback):
        while True:
            data = q.get()
            if rec.AcceptWaveform(data):
                res = json.loads(rec.Result())
                text = res.get('text', '')
                if text:
                    print(f"[FINAL] Heard: {text}")
            else:
                res = json.loads(rec.PartialResult())
                text = res.get('partial', '')
                if text:
                    print(f"[PARTIAL] Hearing: {text}")
except KeyboardInterrupt:
    print("\nTest stopped.")
