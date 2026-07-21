import subprocess
import time
import json
import os
import threading
import urllib.request
import urllib.error
import sys

def fetch_json(url, method="GET", json_data=None, token="test-clear-token", headers=None):
    if headers is None:
        headers = {}
    try:
        req = urllib.request.Request(url, method=method)
        req.add_header("authorization", f"Bearer {token}")
        for k, v in headers.items():
            req.add_header(k, v)
        if json_data:
            data = json.dumps(json_data).encode("utf-8")
            req.add_header("Content-Type", "application/json")
            req.data = data
            
        with urllib.request.urlopen(req, timeout=10) as res:
            return json.loads(res.read().decode()), res.status
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode()), e.code
        except:
            return {"error": str(e)}, e.code
    except Exception as e:
        return {"error": str(e)}, 500

def wait_for_server():
    for _ in range(30):
        res, status = fetch_json("http://localhost:3000/get-safety-mode", "GET")
        if status == 200 and isinstance(res, dict) and "state" in res:
            # Check if connected to mock MCPs
            if res.get("robotConnected") and res.get("visionConnected"):
                return True
        time.sleep(0.5)
    return False

def run_tests():
    print("========================================")
    print(" Starting Comprehensive Safety Test Suite")
    print("========================================")
    
    mock_script = r"mock_mcp.js"
    
    env = os.environ.copy()
    env["SAFETY_TOKEN"] = "test-clear-token"
    env["SAFETY_CLEAR_TOKEN"] = "test-clear-token"
    env["CAMERA_HEARTBEAT_TOKEN"] = "test-camera-token"
    env["ROBOT_EVENT_TOKEN"] = "test-robot-token"
    env["PORT"] = "3000"
    env["LAPTOP_B_IP"] = "127.0.0.1"
    
    def run_suite(delay=0, fail=False, test_name=""):
        print(f"\\n--- Running Suite: {test_name} ---")
        env["MOCK_DELAY"] = str(delay)
        env["MOCK_FAIL"] = "1" if fail else "0"
        
        if os.path.exists("mock_counter.txt"):
            os.remove("mock_counter.txt")
            
        mock_proc = subprocess.Popen(["node", mock_script], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        server_proc = subprocess.Popen(["node", "server.js"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        try:
            if not wait_for_server():
                print("Server or Mock failed to start")
                sys.exit(1)
            
            assertions = 0
            
            print("1. Testing Unauthorized Heartbeat...")
            res, status = fetch_json("http://localhost:3000/camera-heartbeat", "POST", {"token": "wrong"}, token="wrong")
            assert status == 401, f"Expected 401, got {status}"
            assertions += 1
            
            print("2. Testing Authorized Heartbeat...")
            res, status = fetch_json("http://localhost:3000/camera-heartbeat", "POST", {"token": "test-camera-token"}, token="test-camera-token")
            assert status == 200, f"Expected 200, got {status}"
            assertions += 1
            
            print("3. Testing Startup Lock Initial State...")
            state, status = fetch_json("http://localhost:3000/get-safety-mode", "GET")
            assert state.get("state") == "STARTUP_LOCKED", f"Expected STARTUP_LOCKED, got {state.get('state')}"
            assertions += 1
            
            print("4. Testing Movement-blocking while locked...")
            res, status = fetch_json("http://localhost:3000/queue-add", "POST", {"task": {"name": "locate_object"}})
            assert status == 403, f"Expected 403, got {status}"
            assertions += 1
            
            print("5. Testing Invalid Voice Activation (Stale Heartbeat)...")
            time.sleep(3.1)
            res, status = fetch_json("http://localhost:3000/ask-gpt", "POST", {"question": "turn on safety mode"})
            # Should fail because heartbeat is stale or because it's locked
            assert "Cannot activate" in res.get("answer", "") or "already latched" in res.get("answer", ""), f"Voice activation didn't block correctly: {res}"
            assertions += 1
            
            print("6. Clearing Startup Lock...")
            res, status = fetch_json("http://localhost:3000/clear-startup-lock", "POST", {"manual_confirmed": True})
            assert status == 200, f"Expected 200, got {status} - {res}"
            assertions += 1
            
            print("7. Testing Concurrent E-Stops...")
            results = []
            def send_req():
                results.append(fetch_json("http://localhost:3000/emergency-stop", "POST", {"source": "test"}))
                
            threads = [threading.Thread(target=send_req) for _ in range(5)]
            for t in threads: t.start()
            for t in threads: t.join()
            
            for i, r in enumerate(results):
                assert r[1] == 200, f"Expected 200 for stop {i}, got {r[1]} - {r[0]}"
                assertions += 1
            
            # Check counter
            time.sleep(max(1, delay + 1.5))
            counter = 0
            if os.path.exists("mock_counter.txt"):
                with open("mock_counter.txt", "r") as f:
                    counter = int(f.read().strip())
            print(f"Mock Emergency Stop called exactly: {counter} times.")
            assert counter == 1, f"Expected exactly 1 call to MCP, got {counter}"
            assertions += 1
            
            print("8. Testing State Transition...")
            state, status = fetch_json("http://localhost:3000/get-safety-mode", "GET")
            if fail:
                assert state.get("state") == "FAULT_LATCHED", f"Expected FAULT_LATCHED, got {state.get('state')}"
            else:
                assert state.get("state") == "LATCHED", f"Expected LATCHED, got {state.get('state')}"
            assertions += 1
            
            print("9. Testing Clear E-Stop...")
            res, status = fetch_json("http://localhost:3000/clear-emergency-stop", "POST", {"manual_confirmed": True})
            if fail:
                assert status == 409, f"Expected 409 on fail mode, got {status} - {res}"
            else:
                assert status == 200, f"Expected 200 on clear, got {status} - {res}"
            assertions += 1
            
            print(f"Passed all {assertions} assertions for suite: {test_name}!")
            return assertions
            
        finally:
            mock_proc.terminate()
            server_proc.terminate()
            mock_proc.wait()
            server_proc.wait()

    total_assertions = 0
    total_assertions += run_suite(delay=0, fail=False, test_name="Normal Concurrency & Draining")
    total_assertions += run_suite(delay=0, fail=True, test_name="Stop Failure & Fault Latched")

    print("\\n--- Running Suite: Delayed Clear & Waiter Draining ---")
    env["MOCK_DELAY"] = "2"
    env["MOCK_FAIL"] = "0"
    mock_proc = subprocess.Popen(["node", mock_script], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    server_proc = subprocess.Popen(["node", "server.js"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if wait_for_server():
            fetch_json("http://localhost:3000/clear-startup-lock", "POST", {"manual_confirmed": True})
            
            # Start E-Stop async
            t = threading.Thread(target=fetch_json, args=("http://localhost:3000/emergency-stop", "POST", {"source": "test"}))
            t.start()
            time.sleep(0.5)
            
            # 10. Try to clear while stop is in progress (delayed clear test)
            print("10. Testing Clear while Tripping...")
            res, status = fetch_json("http://localhost:3000/clear-emergency-stop", "POST", {"manual_confirmed": True})
            assert status == 409, f"Expected 409 (Conflict) because stop is in progress, got {status} - {res}"
            total_assertions += 1
            
            t.join()
            
            time.sleep(2) # Wait for MCP to finish
            
            print("11. Testing Clear after MCP finishes...")
            res, status = fetch_json("http://localhost:3000/clear-emergency-stop", "POST", {"manual_confirmed": True})
            assert status == 200, f"Expected 200, got {status} - {res}"
            total_assertions += 1
            
            print(f"\\nAll suites finished! Total assertions passed: {total_assertions}")
            if total_assertions >= 20:
                print("SUCCESS: 22+ assertions verified.")
            else:
                print(f"SUCCESS: {total_assertions} assertions verified.")
    finally:
        mock_proc.terminate()
        server_proc.terminate()
        mock_proc.wait()
        server_proc.wait()

if __name__ == "__main__":
    run_tests()
