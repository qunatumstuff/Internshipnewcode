import os
import shutil
import urllib.request
import openwakeword
import openwakeword.utils

# 1. Trigger openwakeword to download all default models to its package directory
print("[PY] Running openwakeword.utils.download_models()...")
openwakeword.utils.download_models()

# 2. Locate the package directory and its models resource folder
package_dir = os.path.dirname(openwakeword.__file__)
models_src_dir = os.path.join(package_dir, "resources", "models")
print(f"[PY] openwakeword package models path: {models_src_dir}")

# 3. Create target models directory
dest_dir = os.path.join(os.path.dirname(__file__), "Public", "models")
os.makedirs(dest_dir, exist_ok=True)
print(f"[PY] Destination models path: {dest_dir}")

# 4. Copy the required model files
for filename in os.listdir(models_src_dir):
    if filename.endswith(".onnx"):
        src_file = os.path.join(models_src_dir, filename)
        dest_file = os.path.join(dest_dir, filename)
        print(f"[PY] Copying {filename} to destination...")
        shutil.copy2(src_file, dest_file)

# 5. Create target ORT WASM directory
ort_dir = os.path.join(os.path.dirname(__file__), "Public", "ort")
os.makedirs(ort_dir, exist_ok=True)
print(f"[PY] Destination ORT path: {ort_dir}")

# 6. Download matching onnxruntime-web WASM files from CDN for local hosting
ort_version = "1.18.0"
wasm_files = {
    "ort-wasm-simd.wasm": f"https://cdn.jsdelivr.net/npm/onnxruntime-web@{ort_version}/dist/ort-wasm-simd.wasm",
    "ort-wasm.wasm": f"https://cdn.jsdelivr.net/npm/onnxruntime-web@{ort_version}/dist/ort-wasm.wasm",
    "ort-wasm-simd-threaded.wasm": f"https://cdn.jsdelivr.net/npm/onnxruntime-web@{ort_version}/dist/ort-wasm-simd-threaded.wasm",
    "ort-wasm-threaded.wasm": f"https://cdn.jsdelivr.net/npm/onnxruntime-web@{ort_version}/dist/ort-wasm-threaded.wasm"
}

for filename, url in wasm_files.items():
    dest_path = os.path.join(ort_dir, filename)
    print(f"[PY] Downloading {filename} from {url}...")
    try:
        urllib.request.urlretrieve(url, dest_path)
        print(f"[PY] Saved {filename} to {dest_path}")
    except Exception as e:
        print(f"[PY] Error downloading {filename}: {e}")

print("[PY] All files downloaded successfully.")
print("Models folder:", os.listdir(dest_dir))
print("ORT folder:", os.listdir(ort_dir))
