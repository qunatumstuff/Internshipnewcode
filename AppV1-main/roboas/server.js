const express = require('express');
const multer = require('multer');
const pdfParse = require('pdf-parse');
const cors = require('cors');
const fs = require('fs');
const path = require('path');
const { Configuration, OpenAIApi } = require('openai');
const { RecursiveCharacterTextSplitter } = require('langchain/text_splitter');
require('langchain/vectorstores/memory');
const { MemoryVectorStore } = require('langchain/vectorstores/memory');
const { OpenAIEmbeddings } = require('@langchain/openai');
// const { SerialPort } = require('serialport'); // Arduino removed
const { exec } = require('child_process');
const https = require('https');
const { Client } = require("@modelcontextprotocol/sdk/client/index.js");
const { StdioClientTransport } = require("@modelcontextprotocol/sdk/client/stdio.js");
const { SSEClientTransport } = require("@modelcontextprotocol/sdk/client/sse.js");
const { search } = require('duck-duck-scrape');
const WebSocket = require('ws');
const http = require('http');
require('dotenv').config();

const WAKEWORD_WS_URL = process.env.WAKEWORD_WS_URL || 'ws://localhost:8003';

// ==========================================
// CONFIGURATION
// ==========================================
// Laptop B (Robot/Vision Laptop) IP Address
const LAPTOP_B_IP = "192.168.2.99"; // Ethernet IP for Laptop B
// === Tool Call Activity Log ===
const toolCallLog = [];
const TOOL_LOG_FILE = path.join(__dirname, 'gpt_tool_log.json');

function logToolCall(userQuestion, toolName, args, result) {
  const entry = {
    timestamp: new Date().toISOString(),
    userQuestion: userQuestion.substring(0, 50) + (userQuestion.length > 50 ? '...' : ''),
    toolName,
    args,
    result
  };
  toolCallLog.push(entry);
  if (toolCallLog.length > 50) toolCallLog.shift(); // Keep last 50 entries
  
  // High-Visibility Colorized Terminal Output
  const cyan = "\x1b[36m";
  const green = "\x1b[32m";
  const yellow = "\x1b[33m";
  const reset = "\x1b[0m";

  console.log("\n" + "=".repeat(50));
  console.log(`🤖 ${cyan}[MCP TOOL TRIGGERED]: ${toolName.toUpperCase()}${reset}`);
  console.log(`❓ User Asked: "${userQuestion}"`);
  console.log(`📦 Arguments:  ${yellow}${JSON.stringify(args)}${reset}`);
  console.log(`✅ Result:     ${green}${result}${reset}`);
  console.log("=".repeat(50) + "\n");

  // Write to disk so Claude Desktop MCP server can read it
  try {
    fs.writeFileSync(TOOL_LOG_FILE, JSON.stringify(toolCallLog, null, 2));
  } catch (e) {
    console.error('❌ \x1b[31mFailed to write tool log to disk:\x1b[0m', e.message);
  }
}

// === MCP Emoji Server Client ===
let mcpEmojiClient = null;
let isEmojiConnected = false;
async function startMcpClient() {
  if (isEmojiConnected) return;
  try {
    const transport = new StdioClientTransport({
      command: "python",
      args: [path.join(__dirname, "mcp_emoji_server.py")]
    });
    mcpEmojiClient = new Client({ name: "roboas-main", version: "1.0.0" }, { capabilities: {} });
    await mcpEmojiClient.connect(transport);
    isEmojiConnected = true;
    console.log("✅ \x1b[32mMCP Emoji Server (Python) connected via Stdio\x1b[0m");
  } catch (err) {
    console.error("❌ \x1b[31mFailed to bind MCP Client:\x1b[0m", err.message);
    isEmojiConnected = false;
    mcpEmojiClient = null;
  }
}
startMcpClient();

// === Wake Word Server (Python) ===
function startWakeWordServer() {
  try {
    const { spawn } = require('child_process');
    const wakeWordProcess = spawn('python', ['-u', path.join(__dirname, 'wakeword_server.py')]);
    
    wakeWordProcess.stdout.on('data', (data) => {
      console.log(`[WAKEWORD]: ${data.toString().trim()}`);
    });
    
    wakeWordProcess.stderr.on('data', (data) => {
      console.error(`[WAKEWORD ERROR]: ${data.toString().trim()}`);
    });
    
    console.log("✅ Python Wake Word Server spawned automatically.");
  } catch (err) {
    console.error("❌ Failed to start Wake Word Server:", err.message);
  }
}
// startWakeWordServer(); // TEMPORARILY DISABLED FOR MANUAL MIC TESTING

// === Vision MCP Server Client (Remote on Laptop B) ===
let visionMcpClient = null;
let isVisionConnected = false;
async function startVisionMcpClient() {
  if (isVisionConnected) return;
  try {
    const transport = new SSEClientTransport(new URL(`http://${LAPTOP_B_IP}:8001/sse`));
    visionMcpClient = new Client({ name: "roboas-main", version: "1.0.0" }, { capabilities: {} });
    await visionMcpClient.connect(transport);
    isVisionConnected = true;
    console.log(`✅ \x1b[32mVision MCP Server connected via SSE at ${LAPTOP_B_IP}:8001\x1b[0m`);
  } catch (err) {
    console.error(`❌ \x1b[31mFailed to bind Vision MCP Client at ${LAPTOP_B_IP}:\x1b[0m`, err.message);
    isVisionConnected = false;
    visionMcpClient = null;
  }
}
startVisionMcpClient();

// === Robot MCP Server Client (Local/Ethernet via SSE) ===
let robotMcpClient = null;
let isRobotConnected = false;
async function startRobotMcpClient() {
  if (isRobotConnected) return;
  try {
    const transport = new SSEClientTransport(new URL("http://localhost:8002/sse"));
    robotMcpClient = new Client({ name: "roboas-robot-mcp", version: "1.0.0" }, { capabilities: {} });
    await robotMcpClient.connect(transport);
    isRobotConnected = true;
    console.log("✅ \x1b[32mRobot MCP Server connected via SSE at localhost:8002\x1b[0m");
  } catch (err) {
    console.error("❌ \x1b[31mFailed to bind Robot MCP Client:\x1b[0m", err.message);
    isRobotConnected = false;
    robotMcpClient = null;
  }
}
startRobotMcpClient();

// Periodic Reconnection Check Loop
setInterval(() => {
  if (!isEmojiConnected) {
    console.log("🔌 Attempting to reconnect to Emoji MCP Server...");
    startMcpClient();
  }
  if (!isVisionConnected) {
    console.log("🔌 Attempting to reconnect to Vision MCP Server...");
    startVisionMcpClient();
  }
  if (!isRobotConnected) {
    console.log("🔌 Attempting to reconnect to Robot MCP Server...");
    startRobotMcpClient();
  }
}, 10000);

async function getStatusEmoji(state) {
  if (!mcpEmojiClient) return state === "answering" ? "🤖" : "🤗";
  try {
    const result = await mcpEmojiClient.callTool({
      name: "get_status_emoji",
      arguments: { state }
    });
    const emoji = result.content[0].text;
    logToolCall("System Status", "get_status_emoji", { state }, `updated to ${emoji}`);
    return emoji;
  } catch (e) {
    console.error("❌ \x1b[31mMCP Tool error:\x1b[0m", e.message);
    isEmojiConnected = false;
    mcpEmojiClient = null;
    return state === "answering" ? "🤖" : "🤗";
  }
}

// Claude Tool Definitions
const CLAUDE_TOOLS = [
  {
    name: "switch_avatar",
    description: "Switch the persona to John (male) or Linda (female) when instructed.",
    input_schema: {
      type: "object",
      properties: {
        persona: { type: "string", enum: ["john", "linda"] }
      },
      required: ["persona"]
    }
  },
  {
      name: "get_status_emoji",
      description: "Get the current status emoji for the bot state (answering or idle).",
      input_schema: {
        type: "object",
        properties: {
          state: { type: "string", enum: ["answering", "idle"] }
        },
        required: ["state"]
      }
  }
];

async function switchAvatar(persona) {
  if (!mcpEmojiClient) {
    currentPersona = persona;
    return persona;
  }
  try {
    const result = await mcpEmojiClient.callTool({
      name: "switch_avatar",
      arguments: { persona }
    });
    currentPersona = persona;
    
    // Log MCP switch action
    logToolCall("System Command", "switch_avatar", { persona }, `switched to ${persona}`);
    
    return result.content[0].text;
  } catch (e) {
    console.error("❌ \x1b[31mMCP Tool error:\x1b[0m", e.message);
    isEmojiConnected = false;
    mcpEmojiClient = null;
    currentPersona = persona;
    return persona;
  }
}



const app = express();
const port = 3000;
const uploadsDir = path.join(__dirname, 'uploads');
if (!fs.existsSync(uploadsDir)) fs.mkdirSync(uploadsDir, { recursive: true });
app.use(express.static(path.join(__dirname, 'Public')));
app.use(cors());
app.use(express.json());

// === OpenAI Configuration ===
const configuration = new Configuration({
  apiKey: "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"
});
const openai = new OpenAIApi(configuration);

// === Multer Storage Setup ===
const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, uploadsDir),
  filename: (req, file, cb) => {
    const uniqueSuffix = Date.now() + '-' + Math.round(Math.random() * 1E9);
    cb(null, file.fieldname + '-' + uniqueSuffix + path.extname(file.originalname));
  }
});
const upload = multer({
  storage,
  fileFilter: (req, file, cb) => {
    const allowedMimeTypes = [
      'application/pdf',
      'audio/mpeg', // .mp3
      'audio/wav',  // .wav
      'audio/ogg',  // .ogg
      'audio/webm', // .webm
      'audio/mp4'   // .mp4 (for audio)
    ];
    if (allowedMimeTypes.includes(file.mimetype)) cb(null, true);
    else cb(new Error('Only PDF or audio files are allowed'), false);
  },
  limits: { fileSize: 25 * 1024 * 1024 } // Increased limit for audio files
});

let chatHistory = [];

// === MCP Emoji Sync Endpoint ===
app.get('/status-emojis', async (req, res) => {
  // Force reset State to John on frontend boot
  currentPersona = "john";
  chatHistory = []; // Wipe conversation memory for new user session
  
  await switchAvatar("john"); // Ensures MCP server resets as well

  const answering = await getStatusEmoji("answering");
  const idle = await getStatusEmoji("idle");
  res.json({ success: true, answering, idle });
});

// === PDF Processing Logic ===
let vectorStore = null;
let currentPdfName = '';
let currentPdfPath = '';
const PDF_TRACKER_FILE = path.join(__dirname, 'current_pdf.json');

async function processPdf(filePath, filename) {
  try {
    console.log(`📄 Processing PDF: ${filename}`);
    const buffer = fs.readFileSync(filePath);
    const data = await pdfParse(buffer);

    if (!data.text || data.text.trim().length === 0)
      throw new Error('PDF is either empty or image-based');

    const splitter = new RecursiveCharacterTextSplitter({ chunkSize: 1000, chunkOverlap: 200 });
    const chunks = await splitter.splitText(data.text);

    const embeddings = new OpenAIEmbeddings({ openAIApiKey: configuration.apiKey, modelName: "text-embedding-ada-002" });
    vectorStore = await MemoryVectorStore.fromTexts(chunks, {}, embeddings);

    currentPdfName = filename;
    currentPdfPath = filePath;

    fs.writeFileSync(PDF_TRACKER_FILE, JSON.stringify({
      filename, path: filePath, uploadedAt: new Date().toISOString()
    }));

    console.log(`✅ PDF processed: ${chunks.length} chunks`);
    return { success: true, chunks: chunks.length, pages: data.numpages || 'unknown' };
  } catch (error) {
    console.error('❌ PDF Processing Error:', error);
    if (fs.existsSync(filePath)) fs.unlinkSync(filePath);
    throw error;
  }
}

async function loadLatestPdf() {
  try {
    if (fs.existsSync(PDF_TRACKER_FILE)) {
      const tracker = JSON.parse(fs.readFileSync(PDF_TRACKER_FILE));
      if (fs.existsSync(tracker.path)) {
        await processPdf(tracker.path, tracker.filename);
        console.log(`📁 Loaded from tracker: ${tracker.filename}`);
        return;
      }
    }

    const files = fs.readdirSync(uploadsDir);
    const pdfs = files.filter(f => f.endsWith('.pdf')).sort().reverse();
    if (pdfs.length > 0) {
      const pdfPath = path.join(uploadsDir, pdfs[0]);
      await processPdf(pdfPath, pdfs[0]);
      console.log(`📁 Loaded fallback PDF: ${pdfs[0]}`);
    } else {
      console.log('⚠️ No PDFs available to load.');
    }
  } catch (err) {
    console.error('❌ Error loading latest PDF:', err.message);
  }
}

// Clear PDF state on startup so each session starts fresh
function clearPdfOnStartup() {
  try {
    // Delete the tracker file so no PDF is remembered
    if (fs.existsSync(PDF_TRACKER_FILE)) {
      fs.unlinkSync(PDF_TRACKER_FILE);
    }
    // Delete all uploaded PDF files so storage stays clean
    if (fs.existsSync(uploadsDir)) {
      const files = fs.readdirSync(uploadsDir);
      files.filter(f => f.endsWith('.pdf')).forEach(f => {
        try { fs.unlinkSync(path.join(uploadsDir, f)); } catch (_) {}
      });
    }
    currentPdfName = '';
    currentPdfPath = '';
    vectorStore = null;
    console.log('🧹 PDF session cleared on startup.');
  } catch (err) {
    console.error('❌ Error clearing PDF on startup:', err.message);
  }
}

// === Dynamic Reasoning Classifier ===
function getReasoningLevel(question) {
  const q = question.toLowerCase().trim();

  // HIGH: complex multi-step, sequential, or planning-heavy instructions
  const highPatterns = [
    /step.*(by|then|after|sequence)/,
    /(then|after that|followed by|next|finally)/,
    /(multiple|several|both|all of the)/,
    /(plan|strategy|sequence|workflow|procedure)/,
    /(pick up.*and.*place|move.*then|rotate.*while)/,
    /(calibrat|troubleshoot|diagnos|explain why|analyse|analyze)/,
    /(compare|difference between|contrast)/,
    /(how do i|how should i|what is the best way)/
  ];

  // LOW: simple status checks, greetings, single-word commands
  const lowPatterns = [
    /^(hi|hello|hey|ok|okay|yes|no|stop|pause|resume)$/,
    /^(what is your name|who are you|status|ready)$/,
    /(switch (to|back)|change (to|back))/,
    /(led (on|off)|turn (on|off))/,
    /^.{0,20}$/ // very short questions (under 20 chars)
  ];

  if (highPatterns.some(p => p.test(q))) {
    console.log(`🧠 Reasoning: HIGH for "${q.substring(0, 40)}"`);
    return "high";
  }
  if (lowPatterns.some(p => p.test(q))) {
    console.log(`⚡ Reasoning: LOW for "${q.substring(0, 40)}"`);
    return "low";
  }
  console.log(`🔄 Reasoning: MEDIUM for "${q.substring(0, 40)}"`);
  return "medium";
}

// === Endpoints ===

// Progress Status SSE Channel
let progressClients = [];
app.get('/progress', (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();

  progressClients.push(res);
  req.on('close', () => {
    progressClients = progressClients.filter(client => client !== res);
  });
});

function sendProgress(status) {
  console.log(`📡 [SSE Progress Broadcast]: "${status}"`);
  progressClients.forEach(client => {
    try {
      client.write(`data: ${JSON.stringify({ status })}\n\n`);
    } catch (e) {
      console.error('❌ SSE write error:', e.message);
    }
  });
}

// Arduino LED endpoint removed.

// Upload PDF
app.post('/upload-pdf', upload.single('pdf'), async (req, res) => {
  if (!req.file) return res.status(400).json({ success: false, message: 'No PDF uploaded.' });
  try {
    const result = await processPdf(req.file.path, req.file.originalname);
    res.json({ success: true, message: 'PDF uploaded.', ...result, filename: req.file.originalname });
  } catch (error) {
    res.status(400).json({ success: false, message: error.message });
  }
});

// Clear / Remove PDF
app.post('/clear-pdf', async (req, res) => {
  try {
    // Delete the physical file
    if (currentPdfPath && fs.existsSync(currentPdfPath)) {
      fs.unlinkSync(currentPdfPath);
    }
    // Delete tracker
    if (fs.existsSync(PDF_TRACKER_FILE)) {
      fs.unlinkSync(PDF_TRACKER_FILE);
    }
    // Reset in-memory state
    currentPdfName = '';
    currentPdfPath = '';
    vectorStore = null;
    console.log('🗑️ PDF cleared by user.');
    res.json({ success: true, message: 'PDF removed.' });
  } catch (err) {
    console.error('❌ Error clearing PDF:', err.message);
    res.status(500).json({ success: false, message: err.message });
  }
});

// Get current PDF status
app.get('/pdf-status', (req, res) => {
  res.json({ loaded: !!currentPdfName, filename: currentPdfName || null });
});

// Transcribe Audio (Whisper STT - Optimized for Option B: Prompt + Regex + LLM)
app.post('/transcribe', upload.single('audio'), async (req, res) => {
  console.log(`\n\n=== 🎙️ [POST /transcribe] Endpoint Hit! ===`);
  if (!req.file) {
    console.log(`❌ [POST /transcribe] Error: No audio file uploaded.`);
    return res.status(400).json({ success: false, message: 'No audio file uploaded.' });
  }
  
  console.log(`✅ [POST /transcribe] Uploaded file size: ${req.file.size} bytes`);
  console.log(`✅ [POST /transcribe] Uploaded MIME type: ${req.file.mimetype}`);

  try {
    // TIER 1: Optimized Context Prompt (Natural flow)
    const promptString = "Hello John and Linda from Roboas! Welcome to Singapore Polytechnic (SP). Could you please calibrate the robotic arm to pick up the screwdriver and check the payload? Switch avatar, or switch back.";

    const transcription = await openai.createTranscription(
      fs.createReadStream(req.file.path),
      "whisper-1",
      promptString,
      undefined,
      0.0, // Zero temperature for maximum accuracy
      "en"
    );

    let recognizedText = transcription.data.text.trim();
    console.log(`[Whisper - Raw STT]: "${recognizedText}"`);

    // TIER 2: Expanded Phonetic Regex Map (Zero latency fast-fix)
    const cleanupMap = {
      "polytechnic": "Polytechnic",
      "sp": "SP",
      "robo us": "Roboas",
      "robots": "Roboas",
      "robot's": "Roboas",
      "robas": "Roboas",
      "robass": "Roboas",
      "robust": "Roboas",
      "row boss": "Roboas",
      "rubber ass": "Roboas",
      "singapore poly": "Singapore Polytechnic",
      "singa poor poly": "Singapore Polytechnic",
      "sp poly": "Singapore Polytechnic",
      "johns": "John's",
      "lindas": "Linda's",
      "lint ah": "Linda",
      "lint up": "Linda"
    };

    for (const [misheard, correct] of Object.entries(cleanupMap)) {
      const regex = new RegExp(`\\b${misheard}\\b`, 'gi');
      recognizedText = recognizedText.replace(regex, correct);
    }
    console.log(`[Whisper - Regex Fixed]: "${recognizedText}"`);

    // TIER 3: LLM Correction Pass (Highest Accuracy, Small Latency)
    // Only bother fixing if the text is longer than a basic yes/no/hi
    if (recognizedText.length > 3) {
      try {
        const correctionCompletion = await openai.createChatCompletion({
          model: "gpt-5.4-mini", // Very fast, cheap proofreader
          temperature: 0.1, // Near deterministic
          messages: [
            {
              role: "system",
              content: `You are a strict STT proofreader for a robotics chatbot called 'Roboas' at 'Singapore Polytechnic'. 
Your ONLY job is to fix phonetically misheard domain words. 
Do NOT answer the question. Do NOT change the meaning. Do NOT add extra punctuation if not necessary.
Return ONLY the corrected text.
Terms to protect:
- 'Roboas' (often misheard as 'robots', 'robust')
- 'John', 'Linda'
- 'calibrate', 'payload', 'screwdriver'`
            },
            { role: "user", content: recognizedText }
          ]
        });

        const correctedOutput = correctionCompletion.data.choices[0].message.content.trim();
        if (correctedOutput && correctedOutput.length > 0) {
          recognizedText = correctedOutput;
          console.log(`[Whisper - LLM Proofread]: "${recognizedText}"`);
        }
      } catch (llmError) {
        console.error('⚠️ LLM Correction Pass Failed (falling back to regex output):', llmError.message);
      }
    }

    // Clean up the temporary audio file
    if (fs.existsSync(req.file.path)) fs.unlinkSync(req.file.path);

    res.json({ success: true, text: recognizedText });
  } catch (error) {
    const errorDetails = error.response ? JSON.stringify(error.response.data) : error.message;
    console.error('❌ Transcription Error:', errorDetails);
    if (fs.existsSync(req.file.path)) fs.unlinkSync(req.file.path);
    res.status(500).json({ success: false, message: 'Transcription failed.', error: errorDetails });
  }
});

// Ask Question from PDF
app.post('/ask-question', async (req, res) => {
  const question = req.body.question;
  if (!question) return res.status(400).json({ success: false, message: 'No question provided.' });

  if (!vectorStore) {
    console.log('🧠 Vector store missing. Reloading...');
    await loadLatestPdf();
    if (!vectorStore) {
      return res.status(400).json({ success: false, message: 'No PDF content available.' });
    }
  }

  // Mute wake word during thinking/processing
  await sendWakewordCommand('mute');

  try {
    const relevantDocs = await vectorStore.similaritySearch(question, 3);
    if (!relevantDocs.length) {
      await sendWakewordCommand('unmute');
      return res.json({ success: true, answer: "I couldn't find relevant information in the document." });
    }

    const context = relevantDocs.map(d => d.pageContent).join('\n\n---\n\n');
    const reasoningLevel = getReasoningLevel(question);
    const completion = await openai.createChatCompletion({
      model: "gpt-5.4-mini",
      reasoning_effort: reasoningLevel,
      messages: [
        {
          role: "system",
          content: `You are a super-friendly and excited AI assistant named John. Answer only from the document "${currentPdfName}" in an upbeat and helpful tone. ` +
            `Make it clear in your response that the information comes from the document. ` +
            `For example: "Wow! According to the document..." or "I'm happy to tell you that the PDF states..."\n` +
            `CRITICAL IDENTITY RULE: NEVER start your response with any introduction (e.g., do NOT say "I am John, your robotic assistant" or "I am John, the LARA 5 assistant"). NEVER repeat your name or role unless the user explicitly asks for it. Start answering the user's question directly and immediately.\n` +
            `CRITICAL OUTPUT CLEANLINESS: DO NOT output any raw coordinate data (e.g. coordinates like x, y, z), tool arguments, or structured JSON/dictionary info. Always output only what the user asked for in a conversational tone. No technical data info.\n` +
            `IMPORTANT: Do not use hyphens (-) in your response.`
        },
        { role: "user", content: `Document:\n${context}\n\nQuestion:\n${question}` }
      ],

      max_tokens: 500
    });

    const answer = cleanChatbotResponse(completion.data.choices[0].message.content);
    const emoji = await getStatusEmoji("answering");
    await sendWakewordCommand('unmute');
    res.json({ success: true, answer, emoji, persona: currentPersona });
  } catch (err) {
    await sendWakewordCommand('unmute');
    const errorDetails = err.response ? JSON.stringify(err.response.data) : err.message;
    console.error('❌ Error in /ask-question:', errorDetails);
    res.status(500).json({ success: false, message: 'AI failed to respond.', error: errorDetails });
  }
});

function parseTextToolCall(text) {
  if (!text) return null;
  const toolNames = ["search_web", "switch_avatar", "locate_object", "get_camera_snapshot", "analyse_surroundings", "pick_and_place_object", "relocate_object"];
  let matchedTool = null;
  
  for (const name of toolNames) {
    if (text.includes(name)) {
      matchedTool = name;
      break;
    }
  }
  
  if (!matchedTool) return null;
  
  const jsonStart = text.indexOf('{');
  const jsonEnd = text.lastIndexOf('}');
  
  if (jsonStart !== -1 && jsonEnd !== -1 && jsonEnd > jsonStart) {
    const jsonStr = text.substring(jsonStart, jsonEnd + 1);
    try {
      const args = JSON.parse(jsonStr);
      return { toolName: matchedTool, args };
    } catch (e) {
      if (matchedTool === "search_web") {
        const queryMatch = text.match(/"query"\s*:\s*"([^"]+)"/);
        if (queryMatch) return { toolName: matchedTool, args: { query: queryMatch[1] } };
      } else if (matchedTool === "locate_object") {
        const targetMatch = text.match(/"target_name"\s*:\s*"([^"]+)"/);
        if (targetMatch) return { toolName: matchedTool, args: { target_name: targetMatch[1] } };
      } else if (matchedTool === "switch_avatar") {
        const personaMatch = text.match(/"persona"\s*:\s*"([^"]+)"/);
        if (personaMatch) return { toolName: matchedTool, args: { persona: personaMatch[1] } };
      } else if (matchedTool === "get_camera_snapshot") {
        const questionMatch = text.match(/"question"\s*:\s*"([^"]+)"/);
        if (questionMatch) return { toolName: matchedTool, args: { question: questionMatch[1] } };
      } else if (matchedTool === "analyse_surroundings") {
        const promptMatch = text.match(/"prompt"\s*:\s*"([^"]+)"/);
        if (promptMatch) return { toolName: matchedTool, args: { prompt: promptMatch[1] } };
      } else if (matchedTool === "pick_and_place_object") {
        const objectMatch = text.match(/"object_name"\s*:\s*"([^"]+)"/);
        const xMatch = text.match(/"x"\s*:\s*([0-9.-]+)/);
        const yMatch = text.match(/"y"\s*:\s*([0-9.-]+)/);
        const zMatch = text.match(/"z"\s*:\s*([0-9.-]+)/);
        const angleMatch = text.match(/"angle_deg"\s*:\s*([0-9.-]+)/);
        if (objectMatch && xMatch && yMatch && zMatch) {
          return {
            toolName: matchedTool,
            args: {
              object_name: objectMatch[1],
              x: parseFloat(xMatch[1]),
              y: parseFloat(yMatch[1]),
              z: parseFloat(zMatch[1]),
              angle_deg: angleMatch ? parseFloat(angleMatch[1]) : undefined
            }
          };
        }
      } else if (matchedTool === "relocate_object") {
        const obstacleMatch = text.match(/"obstacle_name"\s*:\s*"([^"]+)"/);
        const xMatch = text.match(/"obstacle_x"\s*:\s*([0-9.-]+)/);
        const yMatch = text.match(/"obstacle_y"\s*:\s*([0-9.-]+)/);
        const zMatch = text.match(/"obstacle_z"\s*:\s*([0-9.-]+)/);
        const angleMatch = text.match(/"obstacle_angle_deg"\s*:\s*([0-9.-]+)/);
        if (obstacleMatch && xMatch && yMatch && zMatch) {
          return {
            toolName: matchedTool,
            args: {
              obstacle_name: obstacleMatch[1],
              obstacle_x: parseFloat(xMatch[1]),
              obstacle_y: parseFloat(yMatch[1]),
              obstacle_z: parseFloat(zMatch[1]),
              obstacle_angle_deg: angleMatch ? parseFloat(angleMatch[1]) : undefined
            }
          };
        }
      }
    }
  }
  
  if (matchedTool === "search_web") {
    const queryMatch = text.match(/"query"\s*:\s*"([^"]+)"/) || text.match(/query\s*=\s*([^&\n\r]+)/);
    if (queryMatch) {
      return { toolName: matchedTool, args: { query: queryMatch[1] } };
    }
  } else if (matchedTool === "get_camera_snapshot") {
    const questionMatch = text.match(/"question"\s*:\s*"([^"]+)"/) || text.match(/question\s*=\s*([^&\n\r]+)/);
    return { toolName: matchedTool, args: { question: questionMatch ? questionMatch[1] : undefined } };
  } else if (matchedTool === "analyse_surroundings") {
    const promptMatch = text.match(/"prompt"\s*:\s*"([^"]+)"/) || text.match(/prompt\s*=\s*([^&\n\r]+)/);
    return { toolName: matchedTool, args: { prompt: promptMatch ? promptMatch[1] : undefined } };
  }
  
  return null;
}

function cleanChatbotResponse(text) {
  if (!text) return text;
  
  let cleaned = text;
  
  // Strip out tool call markers, target destinations, and raw JSON strings
  cleaned = cleaned.replace(/to=functions\.[a-zA-Z_]+\s*([^\n]+)?/gi, '');
  cleaned = cleaned.replace(/to=[a-zA-Z_]+\s*([^\n]+)?/gi, '');
  cleaned = cleaned.replace(/[a-zA-Z_]+:\s*wuregjson/gi, '');
  cleaned = cleaned.replace(/[a-zA-Z_]+:\s*json/gi, '');
  
  // Strip explicit JSON properties/values
  cleaned = cleaned.replace(/\{[^{}]*"query"[^{}]*\}/gi, '');
  cleaned = cleaned.replace(/\{[^{}]*"target_name"[^{}]*\}/gi, '');
  cleaned = cleaned.replace(/\{[^{}]*"persona"[^{}]*\}/gi, '');
  cleaned = cleaned.replace(/\{[^{}]*"question"[^{}]*\}/gi, '');
  cleaned = cleaned.replace(/\{[^{}]*:[^{}]*\}/gi, '');
  cleaned = cleaned.replace(/```(json)?\s*[\s\S]*?```/gi, '');

  // Strip token-level Chinese/gibberish hallucinations caused by broken stop tokens
  cleaned = cleaned.replace(/天天中彩票有人\s*(json)?/gi, '');
  cleaned = cleaned.replace(/wuregjson/gi, '');

  // Trim extra spaces and duplicates
  cleaned = cleaned.replace(/\n\s*\n+/g, '\n');
  cleaned = cleaned.trim();
  
  return cleaned;
}

function sendWakewordCommand(action) {
  // Client-side openWakeWord-JS handles engine control. No-op on backend.
  return Promise.resolve(true);
}

// === GPT-Powered Chat (Voice + Tools) ===
app.post('/ask-gpt', async (req, res) => {
  const question = req.body.question;
  if (!question) return res.status(400).json({ success: false, message: 'No question provided.' });

  // Mute wake word during thinking/processing
  await sendWakewordCommand('mute');

  try {
    let visualContext = "";
    const lowerQuestion = question.toLowerCase();
    const isVisualQuery = lowerQuestion.includes("what") || lowerQuestion.includes("how");

    if (visionMcpClient && isVisualQuery) {
      try {
        console.log(`[Vision] Prompt contains question keywords. Capturing camera snapshot & asking Qwen: "${question}"`);
        const snapshotRes = await visionMcpClient.callTool({
          name: "get_camera_snapshot",
          arguments: { question: question }
        });
        if (snapshotRes && snapshotRes.content && snapshotRes.content[0]) {
          const answer = snapshotRes.content[0].text;
          visualContext = `\n\nVISUAL CONTEXT (from D435i camera snapshot analysed by Qwen-VL):\n${answer}`;
          console.log(`[Vision] Snapshot visual context retrieved: ${answer}`);
        }
      } catch (e) {
        console.error("Failed to fetch camera snapshot visual context:", e.message);
        isVisionConnected = false;
        visionMcpClient = null;
      }
    }

    let contextStr = "";
    if (vectorStore) {
      const relevantDocs = await vectorStore.similaritySearch(question, 2);
      if (relevantDocs.length > 0) {
        contextStr = `\n\nBACKGROUND KNOWLEDGE (from ${currentPdfName}):\n` +
          relevantDocs.map(d => d.pageContent).join('\n---\n');
      }
    }

    const messages = [
      {
        role: "system",
        content: `You are a helpful, super-excited AI named ${currentPersona === 'linda' ? 'Linda' : 'John'}. You represent LARA 5, a collaborative robot (cobot) by NEURA Robotics at Singapore Polytechnic.

CRITICAL IDENTITY RULES:
- NEVER introduce yourself or state your name, role, or that you are a robotic assistant at the beginning of your responses. Do NOT say "I am John, the LARA 5 robotic assistant" or "I am Linda, the LARA 5 assistant" or anything similar.
- NEVER start your answers with a repetitive introductory formula. Start answering the user's question directly, naturally, and immediately.
- Only state your name or identity if the user explicitly asks "Who are you?", "What is your name?", or similar identity-focused questions.

CRITICAL DUCKDUCKGO / INTERNET ACCESS RULES:
- You DO have direct, real-time access to the internet/web search via the 'search_web' tool (powered by DuckDuckGo and Wikipedia).
- When asked about search engines, DuckDuckGo, or internet access, you MUST clearly, confidently, and enthusiastically declare that you CAN query DuckDuckGo directly in real-time. Never deny having internet search access or claim you are limited to static knowledge.

CRITICAL OBJECT INFERENCE RULE:
- If the user makes an implicit or vague request to pick or locate an item (e.g. expressing a need like being sick, wanting to write, or needing to clean), use your common-sense reasoning to select the most appropriate object from the available tool parameters/enum values and call the tool directly instead of asking for clarification.
- However, if the user explicitly asks for a specific object that is NOT in the available tool parameters/enum values (e.g. a "screwdriver"), you MUST NOT call any locate or pick tool. Instead, politely inform the user that this object is not available in the workspace and list the actual objects you can interact with. Never guess or fall back to an unrelated object like "cube".

CRITICAL OUTPUT CLEANLINESS:
- Do NOT output raw coordinates (e.g. x, y, z values), technical tool arguments, or structured JSON/dictionary info. Keep your responses purely conversational, natural, and concise. Speak about actions in plain English, not data info.

IMPORTANT: Do not use hyphens (-) in your response.\n` + contextStr + visualContext
      },
      ...chatHistory,
      { role: "user", content: question }
    ];

    const reasoningLevel = getReasoningLevel(question);

    // Call GPT with tool support
    const completion = await openai.createChatCompletion({
      model: "gpt-5.4-mini",
      messages: messages,
      tools: [
        {
          type: "function",
          function: {
            name: "switch_avatar",
            description: "Switch the persona to John (male) or Linda (female) when instructed.",
            parameters: {
              type: "object",
              properties: { persona: { type: "string", enum: ["john", "linda"] } },
              required: ["persona"]
            }
          }
        },
        {
          type: "function",
          function: {
            name: "locate_object",
            description: "Uses the robotic vision camera to identify an object and get its coordinates.",
            parameters: {
              type: "object",
              properties: { 
                target_name: { 
                  type: "string", 
                  description: "Name of the object to locate.", 
                  enum: ["black marker", "blue marker", "cube", "green marker", "medicine", "nut", "pipe", "sponge"] 
                } 
              },
              required: ["target_name"]
            }
          }
        },
        {
          type: "function",
          function: {
            name: "search_web",
            description: "Search the internet for real-time information and facts.",
            parameters: {
              type: "object",
              properties: { 
                query: { type: "string", description: "The search query to look up on the web." } 
              },
              required: ["query"]
            }
          }
        },
        {
          type: "function",
          function: {
            name: "get_camera_snapshot",
            description: "Captures a snapshot from the D435i camera to inspect the environment/workspace. Optionally provide a question for the vision model (Qwen-VL) to analyze the image.",
            parameters: {
              type: "object",
              properties: { 
                question: { 
                  type: "string", 
                  description: "Optional question to ask the vision language model about the captured snapshot (e.g., 'what objects are visible?')." 
                } 
              }
            }
          }
        },
        {
          type: "function",
          function: {
            name: "analyse_surroundings",
            description: "Queries the vision MCP to analyze the workspace surroundings using Qwen-VL and describes the layout and objects present.",
            parameters: {
              type: "object",
              properties: { 
                prompt: { 
                  type: "string", 
                  description: "Custom analysis instruction prompt for the model. Defaults to describing objects and layout." 
                } 
              }
            }
          }
        },
        {
          type: "function",
          function: {
            name: "pick_and_place_object",
            description: "Pick and place one detected object using the main robot controller. Input comes from the vision MCP/AI pipeline: object_name, x, y, and z in metres, and optionally angle_deg (yaw in degrees, robot base frame).",
            parameters: {
              type: "object",
              properties: {
                object_name: {
                  type: "string",
                  description: "Object to pick.",
                  enum: ["black marker", "blue marker", "cube", "green marker", "medicine", "nut", "pipe", "sponge"]
                },
                x: { type: "number", description: "Robot-frame X in metres." },
                y: { type: "number", description: "Robot-frame Y in metres." },
                z: { type: "number", description: "Robot-frame Z in metres." },
                angle_deg: { type: "number", description: "Object yaw angle in degrees in robot base frame (optional)." }
              },
              required: ["object_name", "x", "y", "z"]
            }
          }
        },
        {
          type: "function",
          function: {
            name: "relocate_object",
            description: "Pick an obstacle object and move it to a safe empty position within the pick workspace (NOT the placement box), then take a fresh photo. Use this when an object is blocking the target and needs to be moved out of the way first.",
            parameters: {
              type: "object",
              properties: {
                obstacle_name: {
                  type: "string",
                  description: "Name of the object to relocate.",
                  enum: ["black marker", "blue marker", "cube", "green marker", "medicine", "nut", "pipe", "sponge"]
                },
                obstacle_x: { type: "number", description: "Robot-frame X of obstacle in metres." },
                obstacle_y: { type: "number", description: "Robot-frame Y of obstacle in metres." },
                obstacle_z: { type: "number", description: "Robot-frame Z of obstacle in metres." },
                obstacle_angle_deg: { type: "number", description: "Obstacle yaw in degrees (optional)." },
                detections: {
                  type: "array",
                  description: "Full YOLO detection list for the current scene (optional). Used for dynamic obstacle avoidance.",
                  items: { type: "object" }
                }
              },
              required: ["obstacle_name", "obstacle_x", "obstacle_y", "obstacle_z"]
            }
          }
        }
      ],
      tool_choice: "auto",
    });

    const responseMessage = completion.data.choices[0].message;
    let answerText = "";

    // 1. Detect if the model output a tool call as plain text instead of native tool_calls
    let textToolCall = null;
    if (!responseMessage.tool_calls || responseMessage.tool_calls.length === 0) {
      if (responseMessage.content) {
        textToolCall = parseTextToolCall(responseMessage.content);
      }
    }

    // 2. Normalize tool calls into a unified array to process
    let toolCallsToProcess = [];
    let isTextBasedCall = false;

    if (responseMessage.tool_calls && responseMessage.tool_calls.length > 0) {
      toolCallsToProcess = responseMessage.tool_calls.map(tc => ({
        id: tc.id,
        name: tc.function.name,
        arguments: JSON.parse(tc.function.arguments)
      }));
    } else if (textToolCall) {
      isTextBasedCall = true;
      toolCallsToProcess = [{
        id: "call_txt_" + Date.now(),
        name: textToolCall.toolName,
        arguments: textToolCall.args
      }];
      
      // Push the assistant's intermediate message to history
      messages.push({
        role: "assistant",
        content: responseMessage.content
      });
    }

    // 3. Process the tool calls
    if (toolCallsToProcess.length > 0) {
      if (!isTextBasedCall) {
        messages.push(responseMessage); // Push the native assistant message
      }

      for (const toolCall of toolCallsToProcess) {
        const args = toolCall.arguments;
        let toolResultText = "";

        if (toolCall.name === "switch_avatar") {
          await switchAvatar(args.persona);
          logToolCall(question, toolCall.name, args, `switched to ${args.persona}`);
          toolResultText = `Switched to ${currentPersona}. Now greeting the user warmly as ${currentPersona === 'linda' ? 'Linda' : 'John'}.`;
        } 
        else if (toolCall.name === "locate_object") {
          logToolCall(question, "locate_object", args, "Calling Remote Vision MCP...");
          sendProgress(`Initiating workspace scan for "${args.target_name}"...`);
          if (visionMcpClient) {
            try {
              sendProgress(`Capturing frame on Laptop B & running Qwen environment safety scan...`);
              console.log("=================================");
              console.log("RAW LOCATE TOOL CALL");
              console.log("Tool:", "locate_object");
              console.log("Args:", JSON.stringify(args, null, 2));
              console.log("=================================");
              const res = await visionMcpClient.callTool({ name: "locate_object", arguments: args });
              toolResultText = res.content[0].text;
              logToolCall(question, "locate_object", args, "Success");
              
              // Parse progress details for premium UI status updates
              try {
                const parsed = JSON.parse(toolResultText);
                if (parsed.status && parsed.status.startsWith("SUCCESS") && parsed.coordinates) {
                  let coords = parsed.coordinates;
                  console.log(`📍 \x1b[35m[RAW VISION COORDS]:\x1b[0m X: ${coords.x}, Y: ${coords.y}, Z: ${coords.z}`);
                  
                  // Check if the coordinates are in the camera frame and need transformation
                  // Robot workspace X is 0.25 to 0.585. Camera X is usually negative or small.
                  // Z in camera is around 0.9-1.0m, whereas robot Z is 0.0 to 0.8.
                  if (coords.x < 0.2 || coords.z > 0.8) {
                    console.log("🔄 Transforming camera coordinates to robot base frame...");
                    const xc = coords.x;
                    const yc = coords.y;
                    const zc = coords.z;
                    
                    const xr = 0.7337634310 * xc + 0.6126652048 * yc - 0.2936538341 * zc + 0.7173839756;
                    const yr = 0.6785283256 * xc - 0.6388791698 * yc + 0.3625365054 * zc - 0.4903506740;
                    const zr = 0.0345041846 * xc - 0.4652684744 * yc - 0.8844968672 * zc + 0.7880605490;
                    
                    coords.x = xr;
                    coords.y = yr;
                    coords.z = zr;
                    
                    parsed.coordinates = coords;
                    toolResultText = JSON.stringify(parsed);
                    console.log(`📍 \x1b[35m[COORDINATES TRANSFORMED]:\x1b[0m`);
                    console.log(`   Raw Camera:  X: ${xc.toFixed(4)}, Y: ${yc.toFixed(4)}, Z: ${zc.toFixed(4)}`);
                    console.log(`   Robot Base:  \x1b[32mX: ${xr.toFixed(4)}, Y: ${yr.toFixed(4)}, Z: ${zr.toFixed(4)}\x1b[0m`);
                  }
                  
                  sendProgress(`Success! YOLO localized "${args.target_name}" at X: ${coords.x.toFixed(3)}, Y: ${coords.y.toFixed(3)}, Z: ${coords.z.toFixed(3)}. Directing robotic arm to move...`);
                  await new Promise(resolve => setTimeout(resolve, 2500));
                } else if (parsed.status && parsed.status.startsWith("BLOCKED")) {
                  sendProgress(`Blocked: Qwen safety gate determined pickup is NOT safe!`);
                  await new Promise(resolve => setTimeout(resolve, 3000));
                } else if (parsed.status) {
                  sendProgress(parsed.status);
                  await new Promise(resolve => setTimeout(resolve, 2000));
                }
              } catch (jsonErr) {
                sendProgress(`Target scan completed. Generating final response...`);
              }
            } catch (e) {
              toolResultText = `Error calling Vision MCP: ${e.message}`;
              sendProgress(`Error: ${e.message}`);
              logToolCall(question, "locate_object", args, `Failed: ${e.message}`);
              isVisionConnected = false;
              visionMcpClient = null;
              await new Promise(resolve => setTimeout(resolve, 2000));
            }
          } else {
            toolResultText = "Error: Vision MCP is not connected.";
            sendProgress("Error: Remote Vision MCP is not connected.");
            logToolCall(question, "locate_object", args, "Failed: Not Connected");
            await new Promise(resolve => setTimeout(resolve, 2000));
          }
        }
        else if (toolCall.name === "search_web") {
          logToolCall(question, "search_web", args, "Searching web...");
          sendProgress(`Searching the web for "${args.query}"...`);
          try {
            const searchResults = await search(args.query);
            const topResults = searchResults.results.slice(0, 4).map(r => `Title: ${r.title}\nSnippet: ${r.description}\nURL: ${r.url}`).join('\n\n');
            toolResultText = `Search Results for '${args.query}':\n\n${topResults || "No results found."}`;
            logToolCall(question, "search_web", args, `Found results`);
          } catch (err) {
            console.log(`DDG failed, falling back to Wikipedia: ${err.message}`);
            try {
              // Fallback to free Wikipedia API
              const wikiRes = await fetch(`https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=${encodeURIComponent(args.query)}&utf8=&format=json`);
              const wikiData = await wikiRes.json();
              if (wikiData.query && wikiData.query.search && wikiData.query.search.length > 0) {
                const topResults = wikiData.query.search.slice(0, 4).map(r => `Title: ${r.title}\nSnippet: ${r.snippet.replace(/<[^>]*>?/gm, '')}`).join('\n\n');
                toolResultText = `Search Results (Wikipedia) for '${args.query}':\n\n${topResults}`;
                logToolCall(question, "search_web", args, `Found Wikipedia results`);
              } else {
                throw new Error("No Wikipedia results found.");
              }
            } catch (wikiErr) {
              toolResultText = `Search failed: ${err.message}. Wikipedia fallback also failed: ${wikiErr.message}`;
              logToolCall(question, "search_web", args, `Failed: DDG and Wiki both failed.`);
            }
          }
        }
        else if (toolCall.name === "get_camera_snapshot") {
          logToolCall(question, "get_camera_snapshot", args, "Calling Remote Vision MCP...");
          sendProgress("Capturing camera snapshot...");
          if (visionMcpClient) {
            try {
              const res = await visionMcpClient.callTool({ name: "get_camera_snapshot", arguments: args });
              toolResultText = res.content[0].text;
              logToolCall(question, "get_camera_snapshot", args, "Success");
              sendProgress("Snapshot retrieved successfully.");
              await new Promise(resolve => setTimeout(resolve, 1500));
            } catch (e) {
              toolResultText = `Error calling Vision MCP: ${e.message}`;
              sendProgress(`Error: ${e.message}`);
              logToolCall(question, "get_camera_snapshot", args, `Failed: ${e.message}`);
              isVisionConnected = false;
              visionMcpClient = null;
              await new Promise(resolve => setTimeout(resolve, 2000));
            }
          } else {
            toolResultText = "Error: Vision MCP is not connected.";
            sendProgress("Error: Remote Vision MCP is not connected.");
            logToolCall(question, "get_camera_snapshot", args, "Failed: Not Connected");
            await new Promise(resolve => setTimeout(resolve, 2000));
          }
        }
        else if (toolCall.name === "analyse_surroundings") {
          logToolCall(question, "analyse_surroundings", args, "Calling Remote Vision MCP...");
          sendProgress("Analyzing surroundings...");
          if (visionMcpClient) {
            try {
              const res = await visionMcpClient.callTool({ name: "analyse_surroundings", arguments: args });
              toolResultText = res.content[0].text;
              logToolCall(question, "analyse_surroundings", args, "Success");
              sendProgress("Analysis complete.");
              await new Promise(resolve => setTimeout(resolve, 1500));
            } catch (e) {
              toolResultText = `Error calling Vision MCP: ${e.message}`;
              sendProgress(`Error: ${e.message}`);
              logToolCall(question, "analyse_surroundings", args, `Failed: ${e.message}`);
              isVisionConnected = false;
              visionMcpClient = null;
              await new Promise(resolve => setTimeout(resolve, 2000));
            }
          } else {
            toolResultText = "Error: Vision MCP is not connected.";
            sendProgress("Error: Remote Vision MCP is not connected.");
            logToolCall(question, "analyse_surroundings", args, "Failed: Not Connected");
            await new Promise(resolve => setTimeout(resolve, 2000));
          }
        }
        else if (toolCall.name === "pick_and_place_object") {
          logToolCall(question, "pick_and_place_object", args, "Calling Robot MCP...");
          sendProgress(`Executing pick-and-place for "${args.object_name}"...`);
          if (robotMcpClient) {
            try {
              const res = await robotMcpClient.callTool({ name: "pick_and_place_object", arguments: args });
              toolResultText = res.content[0].text;
              logToolCall(question, "pick_and_place_object", args, "Success");
              sendProgress(`Pick-and-place completed for "${args.object_name}".`);
              await new Promise(resolve => setTimeout(resolve, 2000));
            } catch (e) {
              toolResultText = `Error calling Robot MCP pick_and_place_object: ${e.message}`;
              sendProgress(`Error: ${e.message}`);
              logToolCall(question, "pick_and_place_object", args, `Failed: ${e.message}`);
              isRobotConnected = false;
              robotMcpClient = null;
              await new Promise(resolve => setTimeout(resolve, 2000));
            }
          } else {
            toolResultText = "Error: Robot MCP is not connected.";
            sendProgress("Error: Robot MCP is not connected.");
            logToolCall(question, "pick_and_place_object", args, "Failed: Not Connected");
            await new Promise(resolve => setTimeout(resolve, 2000));
          }
        }
        else if (toolCall.name === "relocate_object") {
          logToolCall(question, "relocate_object", args, "Calling Robot MCP...");
          sendProgress(`Relocating obstacle "${args.obstacle_name}"...`);
          if (robotMcpClient) {
            try {
              const res = await robotMcpClient.callTool({ name: "relocate_object", arguments: args });
              toolResultText = res.content[0].text;
              logToolCall(question, "relocate_object", args, "Success");
              sendProgress(`Relocated "${args.obstacle_name}" to a safe spot.`);
              await new Promise(resolve => setTimeout(resolve, 2000));
            } catch (e) {
              toolResultText = `Error calling Robot MCP relocate_object: ${e.message}`;
              sendProgress(`Error: ${e.message}`);
              logToolCall(question, "relocate_object", args, `Failed: ${e.message}`);
              isRobotConnected = false;
              robotMcpClient = null;
              await new Promise(resolve => setTimeout(resolve, 2000));
            }
          } else {
            toolResultText = "Error: Robot MCP is not connected.";
            sendProgress("Error: Robot MCP is not connected.");
            logToolCall(question, "relocate_object", args, "Failed: Not Connected");
            await new Promise(resolve => setTimeout(resolve, 2000));
          }
        }

        messages.push({
          role: "tool",
          tool_call_id: toolCall.id,
          content: toolResultText
        });
      }

      const secondCompletion = await openai.createChatCompletion({
        model: "gpt-5.4-mini",
        messages: messages,
        reasoning_effort: reasoningLevel
      });
      answerText = secondCompletion.data.choices[0].message.content;
    } else {
      answerText = responseMessage.content;
    }

    // 4. Clean up any leftover tool calling traces or token spam from the final answer text
    answerText = cleanChatbotResponse(answerText);

    const emoji = await getStatusEmoji("answering");

    // Sync Chat History
    chatHistory.push({ role: "user", content: question });
    chatHistory.push({ role: "assistant", content: answerText });
    if (chatHistory.length > 10) chatHistory = chatHistory.slice(-10);

    sendProgress(null); // Clear progress overlay
    await sendWakewordCommand('unmute');
    res.json({ success: true, answer: answerText, emoji, persona: currentPersona });

  } catch (err) {
    sendProgress(null); // Clear progress overlay on error
    await sendWakewordCommand('unmute');
    const errorDetails = err.response ? JSON.stringify(err.response.data) : err.message;
    console.error('❌ AI Chat Error:', errorDetails);
    res.status(500).json({ success: false, message: 'AI failed to respond.', error: errorDetails });
  }
});

// === Hybrid TTS Endpoint (OpenAI Primary, Espeak Fallback) ===
app.post('/tts', async (req, res) => {
  const { text } = req.body;
  if (!text) return res.status(400).json({ success: false, message: 'No text provided.' });

  console.log(`🎙️ Generating TTS for: "${text.substring(0, 30)}..."`);

  // 1. Try OpenAI TTS (Premium)
  // Accept persona override from request body for guaranteed voice matching
  const voicePersona = req.body.persona || currentPersona;
  const postData = JSON.stringify({
    model: "tts-1",
    voice: voicePersona === "linda" ? "nova" : "onyx",
    input: text
  });

  const options = {
    hostname: 'api.openai.com',
    path: '/v1/audio/speech',
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${configuration.apiKey}`,
      'Content-Type': 'application/json',
    }
  };

  const openaiReq = https.request(options, (openaiRes) => {
    if (openaiRes.statusCode === 200) {
      console.log('✅ OpenAI TTS Success');
      res.setHeader('Content-Type', 'audio/mpeg');
      openaiRes.pipe(res);
    } else {
      openaiRes.on('data', (d) => {
        console.error(`⚠️ OpenAI TTS Error Detail: ${d.toString()}`);
      });
      console.error(`⚠️ OpenAI TTS Status: ${openaiRes.statusCode}. Falling back to espeak...`);
      runEspeakFallback(text, res);
    }
  });

  openaiReq.on('error', (e) => {
    console.error(`❌ OpenAI Request Error: ${e.message}. Falling back to espeak...`);
    runEspeakFallback(text, res);
  });

  openaiReq.write(postData);
  openaiReq.end();
});

function runEspeakFallback(text, res) {
  console.log('🗣️ Running local espeak fallback...');
  const tempFile = path.join(__dirname, `temp_voice_${Date.now()}.wav`);
  // -w saves to wav, -s 150 for speed, -p 40 for lower pitch
  const command = `espeak-ng -w "${tempFile}" -s 150 -p 40 "${text.replace(/"/g, '')}"`;

  exec(command, (error, stdout, stderr) => {
    if (error) {
      console.error('❌ Espeak failed execution:', error.message);
      console.error('❌ Espeak stderr:', stderr);
      return res.status(500).json({ success: false, message: 'TTS Espeak Failed', error: error.message });
    }

    if (fs.existsSync(tempFile)) {
      res.setHeader('Content-Type', 'audio/wav');
      const stream = fs.createReadStream(tempFile);
      stream.pipe(res);
      stream.on('end', () => {
        try { fs.unlinkSync(tempFile); } catch (e) { } // Cleanup
      });
    } else {
      res.status(500).send('Fallback failed');
    }
  });
}

// === Tool Log Endpoint (for Claude Desktop MCP) ===
app.get('/tool-log', (req, res) => {
  res.json({
    success: true,
    currentPersona,
    totalCalls: toolCallLog.length,
    log: toolCallLog.slice(-20), // Last 20 tool calls
    mcpStatus: {
      emoji: isEmojiConnected,
      vision: isVisionConnected,
      robot: isRobotConnected
    }
  });
});

// === Debug Trigger Mock Tool Endpoint ===
app.post('/debug/trigger-mock-tool', (req, res) => {
  const { toolName, args } = req.body;
  if (!toolName) {
    return res.status(400).json({ success: false, message: 'Missing toolName' });
  }
  
  console.log(`[DEBUG HUD] Mocking tool call: ${toolName}`);
  
  let mockResult = "Success (Mock)";
  
  // If we mock locate_object, simulate coordinate translation!
  if (toolName === "locate_object") {
    let rawX = args.x !== undefined ? Number(args.x) : -0.1009;
    let rawY = args.y !== undefined ? Number(args.y) : -0.1316;
    let rawZ = args.z !== undefined ? Number(args.z) : 0.9670;
    
    // Transform coordinates
    const xr = 0.7337634310 * rawX + 0.6126652048 * rawY - 0.2936538341 * rawZ + 0.7173839756;
    const yr = 0.6785283256 * rawX - 0.6388791698 * rawY + 0.3625365054 * rawZ - 0.4903506740;
    const zr = 0.0345041846 * rawX - 0.4652684744 * rawY - 0.8844968672 * rawZ + 0.7880605490;
    
    console.log(`📍 \x1b[35m[MOCK COORDINATES TRANSFORMED]:\x1b[0m`);
    console.log(`   Raw Camera:  X: ${rawX.toFixed(4)}, Y: ${rawY.toFixed(4)}, Z: ${rawZ.toFixed(4)}`);
    console.log(`   Robot Base:  \x1b[32mX: ${xr.toFixed(4)}, Y: ${yr.toFixed(4)}, Z: ${zr.toFixed(4)}\x1b[0m`);
    
    mockResult = JSON.stringify({
      status: `SUCCESS: Localized ${args.target_name || "object"}`,
      coordinates: { x: xr, y: yr, z: zr },
      raw_coordinates: { x: rawX, y: rawY, z: rawZ }
    });
  } else if (toolName === "pick_and_place_object" || toolName === "relocate_object") {
    mockResult = `Completed: mock action for ${toolName} finished successfully.`;
  }
  
  logToolCall("Mock Debugger Trigger", toolName, args, mockResult);
  
  res.json({
    success: true,
    toolName,
    args,
    result: mockResult
  });
});

// === Debug Clear Logs Endpoint ===
app.post('/debug/clear-logs', (req, res) => {
  console.log(`[DEBUG HUD] Clearing tool log history...`);
  toolCallLog.length = 0; // Empty the in-memory array
  try {
    fs.writeFileSync(TOOL_LOG_FILE, JSON.stringify(toolCallLog, null, 2));
    res.json({ success: true, message: 'Logs cleared successfully' });
  } catch (err) {
    res.status(500).json({ success: false, message: `Failed to clear logs: ${err.message}` });
  }
});

// Debug vector state
app.get('/debug-vectorstore', (req, res) => {
  if (!vectorStore) return res.json({ status: 'No vector store initialized' });
  res.json({ status: 'Vector store exists', pdfName: currentPdfName });
});

// Force reload PDF
app.get('/force-reload', async (req, res) => {
  await loadLatestPdf();
  res.json({ success: true, message: 'Force reloaded latest PDF.' });
});

// Get current PDF info
app.get('/current-pdf', (req, res) => {
  if (!currentPdfPath || !fs.existsSync(currentPdfPath)) {
    return res.json({ hasPdf: false });
  }
  res.json({
    hasPdf: true,
    filename: currentPdfName,
    storedFilename: path.basename(currentPdfPath),
    uploadedAt: fs.statSync(currentPdfPath).mtime.toISOString()
  });
});

// Get raw PDF file
app.get('/get-pdf', (req, res) => {
  if (!currentPdfPath || !fs.existsSync(currentPdfPath)) {
    return res.status(404).json({ success: false, message: 'No PDF available.' });
  }
  res.setHeader('Content-Type', 'application/pdf');
  res.setHeader('Content-Disposition', `inline; filename="${currentPdfName}"`);
  res.sendFile(currentPdfPath);
});

// Cleanup old files (over 3 days old)
function cleanupOldFiles() {
  const now = Date.now();
  const maxAge = 3 * 24 * 60 * 60 * 1000;
  fs.readdirSync(uploadsDir).forEach(file => {
    const filePath = path.join(uploadsDir, file);
    const stats = fs.statSync(filePath);
    if (now - stats.mtimeMs > maxAge) {
      fs.unlinkSync(filePath);
      console.log(`🧹 Deleted old file: ${file}`);
    }
  });
}

// Ask Claude to inspect tool usage
app.post('/ask-claude', async (req, res) => {
  const { question } = req.body;
  if (!question) return res.status(400).json({ success: false, message: 'No question provided.' });

  // Mute wake word during thinking/processing
  await sendWakewordCommand('mute');

  try {
    const recentTools = toolCallLog.slice(-10); // Last 10 tool calls
    const toolSummary = recentTools.length === 0
      ? 'No tools have been called yet in this session.'
      : recentTools.map((t, i) =>
        `[${i + 1}] At ${t.timestamp}:\n  User asked: "${t.userQuestion}"\n  GPT called tool: ${t.toolName}\n  Args: ${JSON.stringify(t.args)}\n  Result: ${t.result}`
      ).join('\n\n');

    const message = await anthropic.messages.create({
      model: 'claude-opus-4-5',
      max_tokens: 1024,
      messages: [
        {
          role: 'user',
          content: `You are a helpful AI assistant monitoring the Roboas chatbot system. Here is the recent tool usage log from GPT:\n\n${toolSummary}\n\nUser question: ${question}`
        }
      ]
    });

    const answer = message.content[0].text;
    console.log(`🧠 Claude response: ${answer.substring(0, 80)}...`);
    await sendWakewordCommand('unmute');
    res.json({ success: true, answer, toolLog: recentTools });
  } catch (err) {
    await sendWakewordCommand('unmute');
    console.error('❌ Claude error:', err.message);
    res.status(500).json({ success: false, message: err.message });
  }
});

// Switch Persona from Flutter FAB (Hard Sync with Brain)
app.post('/switch-persona', (req, res) => {
  const { persona, silent } = req.body;
  if (!persona || !['john', 'linda'].includes(persona)) {
    return res.status(400).json({ success: false, message: 'Invalid persona.' });
  }
  currentPersona = persona;

  // 1. Log manual switch for transparency (Suppress if it's an internal AI sync)
  if (!silent) {
    logToolCall("System Sync", "switch_avatar", { persona }, `switched to ${persona} via Remote Control`);
  }

  console.log(`🔄 Persona switched to: ${persona} (Brain Synced ${silent ? ' - SILENT' : ''})`);
  res.json({ success: true, persona });
});

// Serve debug dashboard
app.get('/debug', (req, res) => {
  res.setHeader('Cache-Control', 'no-store'); // Ensure fresh HUD on reload
  res.sendFile(path.join(__dirname, 'resources', 'debug.html'));
});

// Serve index.html
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'Public', 'index.html'));
});

// Start server with WebSocket proxy for wake word
const server = http.createServer(app);
// WebSocket proxy for Python wake word is disabled in browser-based OWW flow.
// const wss = new WebSocket.Server({ server, path: '/wakeword' });

server.listen(port, async () => {
  console.log(`🚀 Server running at http://localhost:${port}`);
  console.log(`🔊 Client-side openWakeWord engine active (Vosk WS proxy disabled)`);
  clearPdfOnStartup(); // Always start fresh — no PDF memory between sessions
  
  // Auto-load the LARA datasheet from resources so the robot has product knowledge
  const laraPath = path.join(__dirname, 'resources', 'LARA_NEURA_Robotics_Datasheet_Web.pdf');
  if (fs.existsSync(laraPath)) {
    try {
      await processPdf(laraPath, 'LARA_NEURA_Robotics_Datasheet_Web.pdf');
      console.log('📄 Auto-loaded LARA datasheet from resources/');
    } catch (e) {
      console.error('⚠️ Failed to auto-load LARA datasheet:', e.message);
    }
  }
  
  cleanupOldFiles();
  setInterval(cleanupOldFiles, 24 * 60 * 60 * 1000);
});
