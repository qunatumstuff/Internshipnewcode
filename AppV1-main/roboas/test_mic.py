import sounddevice as sd
import numpy as np
import scipy.io.wavfile as wavfile
import math
import sys

def test_microphone():
    samplerate = 16000
    duration = 3.0  # seconds
    device_id = 1
    
    print(f"🎤 Starting mic test on device {device_id}...")
    print(f"Recording for {duration} seconds. Please speak now!")
    
    try:
        # Record audio
        recording = sd.rec(int(duration * samplerate), samplerate=samplerate, channels=1, dtype='int16', device=device_id)
        sd.wait()  # Wait until recording is finished
        print("✅ Recording finished.")
        
        # Calculate RMS volume
        recording_float = recording.astype(np.float32)
        sum_sq = np.sum(recording_float ** 2)
        rms = math.sqrt(sum_sq / len(recording_float))
        
        print(f"📊 Audio Level (RMS): {rms:.2f}")
        if rms < 50:
            print("⚠️ WARNING: Audio is very quiet or completely silent. The device might not be capturing audio properly.")
        else:
            print("🔊 Good audio level detected!")
            
        # Save to WAV file
        wavfile.write("test_mic.wav", samplerate, recording)
        print("💾 Saved recording to 'test_mic.wav'.")
        print("\nPlay it back manually to verify the audio quality!")
        
    except Exception as e:
        print(f"❌ Error during recording: {e}")
        print("\nAvailable devices:")
        print(sd.query_devices())

if __name__ == "__main__":
    test_microphone()
