
import sys, json, time, os
def main():
    call_count = 0
    fail_mode = os.environ.get("MOCK_FAIL", "0") == "1"
    delay = int(os.environ.get("MOCK_DELAY", "0"))
    while True:
        line = sys.stdin.readline()
        if not line: break
        try:
            req = json.loads(line)
            if "method" in req and req["method"] == "tools/call":
                name = req["params"]["name"]
                if name == "emergency_stop":
                    call_count += 1
                    if delay > 0:
                        time.sleep(delay)
                    if fail_mode:
                        res = {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32000, "message": "Hardware Error 0x99"}}
                    else:
                        res = {"jsonrpc": "2.0", "id": req["id"], "result": {"content": [{"type": "text", "text": f"Stop confirmed. Count {call_count}"}]}}
                    with open("mock_counter.txt", "w") as f:
                        f.write(str(call_count))
                elif name == "clear_emergency_stop":
                    if req["params"]["arguments"].get("token") != "default-secure-token-xyz":
                        res = {"jsonrpc": "2.0", "id": req["id"], "result": {"content": [{"type": "text", "text": "Error: Invalid token"}]}}
                    else:
                        res = {"jsonrpc": "2.0", "id": req["id"], "result": {"content": [{"type": "text", "text": "Clear confirmed."}]}}
                elif name == "clear_startup_lock":
                    res = {"jsonrpc": "2.0", "id": req["id"], "result": {"content": [{"type": "text", "text": "Startup cleared."}]}}
                else:
                    res = {"jsonrpc": "2.0", "id": req["id"], "result": {"content": [{"type": "text", "text": "Ok"}]}}
                print(json.dumps(res), flush=True)
            elif "method" in req and req["method"] == "tools/list":
                print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": {"tools": [{"name": "emergency_stop"}, {"name": "clear_emergency_stop"}, {"name": "clear_startup_lock"}]}}), flush=True)
            else:
                print(json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": {}}), flush=True)
        except Exception as e:
            pass
main()
