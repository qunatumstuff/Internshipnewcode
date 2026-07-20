
const fs = require('fs');
let code = fs.readFileSync('server.js', 'utf8');
code = code.replace("python robot_mcp.py", "python mock_robot_mcp.py");
code = code.replace("python vision_mcp.py", "python -c 'while True: pass'");
fs.writeFileSync('server_test.js', code);
