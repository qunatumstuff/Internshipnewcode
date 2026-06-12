# System Architecture & Ngrok Premium Justification Report

This document outlines the distributed architecture of the AI-driven Robotic Chatbot system (Roboas) and provides a technical justification for upgrading to **Ngrok Premium** for deployment and testing.

---

## 1. Visual System Architecture Diagram

Below is the generated system architecture diagram illustrating the network flow, component locations, and how Ngrok acts as the secure ingress gateway for client operations:

![System Architecture Diagram](file:///C:/Users/Dominic/.gemini/antigravity/brain/6c67177b-db98-4266-8e78-a0c226812ed1/system_architecture_1780647202182.png)

---

## 2. Interactive Flowchart (Mermaid)

This vector flowchart shows how the systems interface across WAN and local Ethernet subnets:

```mermaid
graph TD
    subgraph WAN [Public WAN (Internet)]
        Client["Operator UI (Flutter Web App)<br>- Voice Upload (WAV)<br>- Audio Playback (TTS)<br>- Emergency Stop Trigger"]
        Ngrok["Secure Ngrok TLS Tunnel<br>(HTTPS / WSS / TLS Edge)"]
    end

    subgraph LAN [Local Network (Ethernet/Wi-Fi)]
        LaptopA["Laptop A: Central Orchestrator (Port 3000)<br>- server.js (Node.js)<br>- LangChain Memory Vector Store<br>- Coordinate Transformation Matrix"]
        LaptopB["Laptop B: Vision Server (Port 8001)<br>- vision_mcp.py (FastMCP)<br>- YOLO Segmenter (best13.pt)<br>- Ollama Qwen3-VL Model"]
        RobotPC["Robot PC: Controller (Port 8002)<br>- robot_mcp.py (MCP SSE)<br>- Neura Robotics SDK"]
        
        Camera["Intel RealSense Depth Camera"]
        Arm["Neura LARA 5 Robot Arm (IP: 192.168.2.13)"]
    end

    %% Flow links
    Client ===>|Secure HTTPS / WSS| Ngrok
    Ngrok ===>|Tunnel Forwarding| LaptopA
    
    LaptopA ===>|SSE Client Transport (Port 8001)| LaptopB
    LaptopA ===>|SSE Client Transport (Port 8002)| RobotPC
    
    LaptopB --->|Hardware Pipeline| Camera
    RobotPC --->|Direct SDK Connection| Arm
    
    %% Styling
    style Client fill:#3a0ca3,stroke:#7209b7,stroke-width:2px,color:#fff
    style Ngrok fill:#f72585,stroke:#b5179e,stroke-width:2px,color:#fff
    style LaptopA fill:#240046,stroke:#3c096c,stroke-width:2px,color:#fff
    style LaptopB fill:#03045e,stroke:#0077b6,stroke-width:2px,color:#fff
    style RobotPC fill:#004b23,stroke:#38b000,stroke-width:2px,color:#fff
    style Camera fill:#0096c7,stroke:#03045e,stroke-width:1px,color:#fff
    style Arm fill:#70e000,stroke:#007200,stroke-width:1px,color:#fff
```

---

## 3. Security & Encryption: Does Ngrok Encrypt Traffic?

**Yes. Ngrok fully encrypts all communication.**

When you run `ngrok http 3000`, Ngrok exposes a secure public endpoint (e.g., `https://your-session.ngrok-free.app`). 

* **End-to-End TLS Encryption:** All connections initiated by the operator's browser to the Ngrok edge are secured with high-grade **SSL/TLS (HTTPS and WSS)**. No credentials, voice payloads, or robotic command packets are exposed in transit over public networks.
* **Firewall Penetration:** Ngrok opens an outbound TCP tunnel from Laptop A to the Ngrok Edge. This means you do not need to open inbound ports or modify router settings on your local network (which is often blocked by strict corporate firewalls).

---

## 4. Why We Must Buy Ngrok Premium

While the free tier of Ngrok works for basic hello-world APIs, it is **unusable and highly unsafe** for testing and deploying a real-time physical robotic system. Below is the technical justification to convince your supervisor:

### A. Critical Bandwidth Throttling (The Vision & Audio Bottleneck)
* **The Problem:** The system streams high-bandwidth payloads. Speech prompts are recorded as raw WAV audio files and uploaded to the server, while log streams transmit coordinates and diagnostic data. If we add video feedback or crop image transmission from Laptop B's camera, the data requirements spike.
* **Free Tier Limit:** Ngrok's free tier has strict limits on throughput and monthly data transfer (typically **1GB per month**). A single afternoon of active testing and sending voice/image packets will exceed this limit, causing Ngrok to instantly suspend the tunnel.
* **Premium Benefit:** Unlimited/high-throughput bandwidth guarantees that testing sessions never freeze due to data caps.

### B. Connection Rate Limits (120 Connections/Min Cap)
* **The Problem:** This system is highly asynchronous. The web application maintains a persistent WebSocket connection for real-time status updates and makes constant REST API requests (status polls, tool executions, emergency stop triggers, log retrievals). 
* **Free Tier Limit:** The free plan imposes a strict rate limit of **120 requests/minute**. If the frontend makes parallel status queries and sends continuous audio or log chunks, the browser will exceed this limit in minutes. Once throttled, the frontend loses connection, causing the robot controls to drop or fail mid-task.
* **Premium Benefit:** Removes rate-limiting constraints, ensuring seamless, low-latency, real-time message delivery.

### C. The Safety & Security Hazard (Critical):
* **The Problem:** A physical robot arm (like the LARA 5) is heavy machinery capable of causing physical injury or property damage if operated incorrectly. The free tier of Ngrok creates a **completely public URL** accessible by anyone on the internet who guesses or intercepts it.
* **Free Tier Limit:** Free Ngrok links do not allow advanced authentication at the edge. Anyone loading the URL can press the control buttons, upload malicious PDFs, or trigger robot motions.
* **Premium Benefit:** Enforces **Edge-level OAuth (e.g., Google or Microsoft SSO)** and **IP Whitelisting**. Only authorized developers and operators can access the chatbot control interface. Requests from unauthorized IPs are rejected at the Ngrok servers before they ever reach your local laptop or robot.

### D. Ephemeral URLs vs. Reserved Static Domains
* **The Problem:** Every time the free Ngrok tunnel restarts, it generates a random URL (e.g., `https://a1b2-34-56.ngrok-free.app`). 
* **Free Tier Limit:** Because the URL changes daily (or on connection drop), the developer must rebuild the Flutter Web app (`flutter build web --release`), copy the build to the Node `Public/` folder, and reconfigure the local MCP configs with the new URL. This wastes hours of engineering time and makes it impossible to bookmark the testing page or share a stable link with stakeholders.
* **Premium Benefit:** Provides a **Reserved Static Domain** (e.g., `https://our-robot-project.ngrok.app`). The URL never changes, meaning the Flutter app and server scripts can be hardcoded once, providing a clean, professional "always-on" demo portal.

---

## 5. Technical Summary for Supervisor Review

| Feature | Free Tier | Premium Tier | Impact on Robotics Project |
| :--- | :--- | :--- | :--- |
| **Bandwidth** | Strict monthly limit (1GB) | Unlimited / High-Throughput | Free tier halts operations mid-run when audio/vision logs exceed the cap. |
| **Rate Limiting** | 120 connections / min | Unrestricted | Free tier drops active WebSockets/SSE connections, severing control of the arm. |
| **Endpoint URL** | Randomly changes on restart | Permanent, Reserved Static Domain | Free tier requires daily re-compilation of Flutter Web assets; Premium is set-and-forget. |
| **Access Security** | None (Publicly exposed) | OAuth (Google/Github), IP Whitelisting | **Safety Critical:** Premium blocks unauthorized users from triggering physical arm movements. |
| **Support for TCP/SSE** | Basic | Enhanced | Ensures SSE message streams remain open indefinitely without timeout. |
