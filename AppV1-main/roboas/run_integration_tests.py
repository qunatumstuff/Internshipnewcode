import subprocess
import time
import requests
import sys
import os
import signal
import threading

def run_tests():
    print("Starting tests...")
    
    # Environment variables
    env = os.environ.copy()
    env["SAFETY_CLEAR_TOKEN"] = "test-clear-token"
    env["CAMERA_HEARTBEAT_TOKEN"] = "test-camera-token"

    # Start the server
    server_proc = subprocess.Popen(["node", "server.js"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(2)  # wait for startup

    if server_proc.poll() is not None:
        print("Server failed to start. Missing token logic works?")
        
    try:
        # Test 1: Start up lock clear (requires manual_confirmed and right token implicitly via backend MCP)
        # Note: the Node server just passes it to MCP. If we don't have MCP, it might return 500.
        resp = requests.post("http://localhost:3000/clear-startup-lock", json={"manual_confirmed": True})
        print(f"Startup clear response (no MCP): {resp.status_code}")
        
        # Test 2: Heartbeat unauthorized
        resp = requests.post("http://localhost:3000/camera-heartbeat", json={"token": "wrong"})
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
        print("Heartbeat auth block works")
        
        # Test 3: Heartbeat authorized
        resp = requests.post("http://localhost:3000/camera-heartbeat", json={"token": "test-camera-token"})
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        print("Heartbeat auth success works")
        
    finally:
        server_proc.kill()
        
    print("Integration test passed!")

if __name__ == "__main__":
    run_tests()
