import urllib.request, json
req = urllib.request.Request('http://192.168.2.99:8002/messages', data=b'{"jsonrpc":"2.0","method":"tools/list","id":1}')
try:
    with urllib.request.urlopen(req) as res:
        print(res.read().decode())
except urllib.error.HTTPError as e:
    print('HTTP ERROR:', e.code, e.read().decode())
except Exception as e:
    print(e)
