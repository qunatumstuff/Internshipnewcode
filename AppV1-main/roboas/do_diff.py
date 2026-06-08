import difflib
import sys

def diff(f1, f2, out):
    with open(f1, encoding='utf-8') as file1:
        with open(f2, encoding='utf-8') as file2:
            d = list(difflib.unified_diff(file1.readlines(), file2.readlines(), fromfile=f1, tofile=f2))
    with open(out, 'w', encoding='utf-8') as out_file:
        out_file.writelines(d)

diff('camera.py', 'camerafixed.py', 'diff_cam.txt')
diff('vision_mcp.py', 'visionfixed.py', 'diff_vis.txt')
