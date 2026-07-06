import urllib.request
import json

payload = {
    "model": "qwen3-vl:2b",
    "messages": [
        {
            "role": "user",
            "content": "hello"
        }
    ],
    "stream": False,
    "options": {"temperature": 0.1, "num_predict": 4096}
}

try:
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as response:
        print("Success:", response.read().decode())
except Exception as e:
    print(f"Error: {e}")
    if hasattr(e, 'read'):
        print(e.read().decode())
