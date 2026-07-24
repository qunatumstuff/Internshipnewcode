import urllib.request
import json
req = urllib.request.Request('http://192.168.2.99:8002/messages', data=b'{"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_estop_state","arguments":{}},"id":1}')
try:
    with urllib.request.urlopen(req) as response:
        print(response.read().decode())
except urllib.error.HTTPError as e:
    print('HTTP ERROR:', e.code, e.read().decode())
except Exception as e:
    print('ERROR:', e)
