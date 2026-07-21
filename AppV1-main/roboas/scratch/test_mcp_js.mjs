import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";

async function main() {
    const transport = new SSEClientTransport(new URL("http://192.168.2.99:8002/sse"));
    const client = new Client({ name: "test-client", version: "1.0.0" }, { capabilities: {} });
    await client.connect(transport);
    console.log("Connected to Robot MCP.");
    
    try {
        console.log("Testing return_home...");
        const result = await client.callTool({ name: "return_home", arguments: {} });
        console.log("return_home result:", result);
    } catch (e) {
        console.error("return_home failed:", e.message);
    }
    
    try {
        console.log("Testing locate_object on Vision MCP...");
        const vtransport = new SSEClientTransport(new URL("http://192.168.2.99:8001/sse"));
        const vclient = new Client({ name: "test-client", version: "1.0.0" }, { capabilities: {} });
        await vclient.connect(vtransport);
        const result = await vclient.callTool({ name: "locate_object", arguments: { target_name: "medicine" } });
        console.log("locate_object result:", result);
    } catch (e) {
        console.error("locate_object failed:", e.message);
    }
    
    process.exit(0);
}

main().catch(console.error);
