import os
import shutil
import openwakeword
import openwakeword.utils

# 1. Trigger openwakeword to download all default models to its package directory
print("[PY] Running openwakeword.utils.download_models()...")
openwakeword.utils.download_models()

# 2. Locate the package directory and its models resource folder
package_dir = os.path.dirname(openwakeword.__file__)
models_src_dir = os.path.join(package_dir, "resources", "models")
print(f"[PY] openwakeword package models path: {models_src_dir}")

# 3. Create target directory
dest_dir = os.path.join(os.path.dirname(__file__), "Public", "models")
os.makedirs(dest_dir, exist_ok=True)
print(f"[PY] Destination models path: {dest_dir}")

# 4. Copy the required model files
# We need: melspectrogram.onnx, embedding_model.onnx, and at least one keyword model (e.g. hey_jarvis.onnx or alexa.onnx)
# Note: the files inside openwakeword python package might be named melspectrogram.onnx, embedding_model.onnx, hey_jarvis.onnx
for filename in os.listdir(models_src_dir):
    if filename.endswith(".onnx"):
        src_file = os.path.join(models_src_dir, filename)
        dest_file = os.path.join(dest_dir, filename)
        # Check if we should copy it (we copy melspectrogram, embedding_model, silero_vad and default keywords)
        print(f"[PY] Copying {filename} to destination...")
        shutil.copy2(src_file, dest_file)

# Let's also check if silero_vad.onnx was downloaded or where it is
# If we need silero_vad, it is often in the package parent folder or cache
# Let's search inside the package directory for any other onnx files
for root, dirs, files in os.walk(package_dir):
    for f in files:
        if f.endswith(".onnx"):
            src_f = os.path.join(root, f)
            dest_f = os.path.join(dest_dir, f)
            if not os.path.exists(dest_f):
                print(f"[PY] Found extra model in package: {f}. Copying...")
                shutil.copy2(src_f, dest_f)

print("[PY] Copying finished. Directory contents:")
print(os.listdir(dest_dir))
