const express = require('express');
const app = express();
app.use(express.json());

let clients = [];
let mockFail = process.env.MOCK_FAIL === '1';
let mockDelay = parseInt(process.env.MOCK_DELAY || '0');

let messageCount = 0;

app.get('/sse', (req, res) => {
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');
    res.flushHeaders();
    
    clients.push(res);
    
    // Send endpoint event
    res.write(`event: endpoint\ndata: http://127.0.0.1:8002/messages\n\n`);
});

app.post('/messages', (req, res) => {
    const msg = req.body;
    res.status(202).send('Accepted');
    
    setTimeout(() => {
        let result = { content: [{ type: "text", text: '{"success":true,"state":"CLEARED"}' }] };
        let isError = false;
        let errorCode = 0;
        
        if (msg.method === "tools/call") {
            const tool = msg.params.name;
            const args = msg.params.arguments;
            
            if (tool === "emergency_stop") {
                messageCount++;
                require('fs').writeFileSync('mock_counter.txt', messageCount.toString());
                if (mockFail) {
                    result = { content: [{ type: "text", text: "Error: hardware fault" }] };
                } else {
                    result = { content: [{ type: "text", text: "Stopped successfully" }] };
                }
            } else if (tool === "clear_emergency_stop") {
                if (mockFail) {
                    isError = true;
                    errorCode = -32000;
                    result = { error: { code: -32000, message: "Fault" } };
                } else if (args.token !== "test-clear-token") {
                    result = { content: [{ type: "text", text: "Error: Invalid capability token" }] };
                } else {
                    result = { content: [{ type: "text", text: '{"success":true,"state":"CLEARED"}' }] };
                }
            } else if (tool === "clear_startup_lock") {
                if (args.token !== "test-clear-token") {
                    result = { content: [{ type: "text", text: "Error: Invalid capability token" }] };
                } else {
                    result = { content: [{ type: "text", text: '{"success":true,"state":"CLEARED"}' }] };
                }
            }
        }
        
        const response = {
            jsonrpc: "2.0",
            id: msg.id,
        };
        
        if (isError) {
            response.error = result.error;
        } else {
            response.result = result;
        }
        
        clients.forEach(c => c.write(`event: message\ndata: ${JSON.stringify(response)}\n\n`));
    }, mockDelay * 1000);
});

app.listen(8002, () => {
    console.log("Mock Robot MCP listening on 8002");
});

app.listen(8001, () => {
    console.log("Mock Vision MCP listening on 8001");
});
