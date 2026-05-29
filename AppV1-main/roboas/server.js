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
const LAPTOP_B_IP = "172.22.11.140"; // Wi-Fi IP for Laptop B
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
  
  // High-Visibility Terminal Output
  console.log("\n" + "=".repeat(50));
  console.log(`🤖 [MCP TOOL TRIGGERED]: ${toolName.toUpperCase()}`);
  console.log(`❓ User Asked: "${userQuestion}"`);
  console.log(`📦 Arguments:  ${JSON.stringify(args)}`);
  console.log(`✅ Result:     ${result}`);
  console.log("=".repeat(50) + "\n");

  // Write to disk so Claude Desktop MCP server can read it
  try {
    fs.writeFileSync(TOOL_LOG_FILE, JSON.stringify(toolCallLog, null, 2));
  } catch (e) {
    console.error('❌ Failed to write tool log to disk:', e.message);
  }
}

// === MCP Emoji Server Client ===
let mcpEmojiClient = null;
async function startMcpClient() {
  try {
    const transport = new StdioClientTransport({
      command: "python",
      args: [path.join(__dirname, "mcp_emoji_server.py")]
    });
    mcpEmojiClient = new Client({ name: "roboas-main", version: "1.0.0" }, { capabilities: {} });
    await mcpEmojiClient.connect(transport);
    console.log("✅ MCP Emoji Server (Python) connected via Stdio");
  } catch (err) {
    console.error("❌ Failed to bind MCP Client:", err.message);
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
async function startVisionMcpClient() {
  try {
    const transport = new SSEClientTransport(new URL(`http://${LAPTOP_B_IP}:8001/sse`));
    visionMcpClient = new Client({ name: "roboas-main", version: "1.0.0" }, { capabilities: {} });
    await visionMcpClient.connect(transport);
    console.log(`✅ Vision MCP Server connected via SSE at ${LAPTOP_B_IP}:8001`);
  } catch (err) {
    console.error(`❌ Failed to bind Vision MCP Client at ${LAPTOP_B_IP}:`, err.message);
  }
}
startVisionMcpClient();

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
    console.error("❌ MCP Tool error:", e.message);
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
    console.error("❌ MCP Tool error:", e.message);
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
  const toolNames = ["search_web", "switch_avatar", "locate_object"];
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
      }
    }
  }
  
  if (matchedTool === "search_web") {
    const queryMatch = text.match(/"query"\s*:\s*"([^"]+)"/) || text.match(/query\s*=\s*([^&\n\r]+)/);
    if (queryMatch) {
      return { toolName: matchedTool, args: { query: queryMatch[1] } };
    }
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
  return new Promise((resolve) => {
    try {
      const ws = new WebSocket(WAKEWORD_WS_URL);
      ws.on('open', () => {
        ws.send(JSON.stringify({ action }));
        ws.close();
        resolve(true);
      });
      ws.on('error', (err) => {
        // Suppress connection errors if server is not fully spawned yet
        resolve(false);
      });
    } catch (e) {
      resolve(false);
    }
  });
}

// === GPT-Powered Chat (Voice + Tools) ===
app.post('/ask-gpt', async (req, res) => {
  const question = req.body.question;
  if (!question) return res.status(400).json({ success: false, message: 'No question provided.' });

  // Mute wake word during thinking/processing
  await sendWakewordCommand('mute');

  try {
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

CRITICAL OUTPUT CLEANLINESS:
- Do NOT output raw coordinates (e.g. x, y, z values), technical tool arguments, or structured JSON/dictionary info. Keep your responses purely conversational, natural, and concise. Speak about actions in plain English, not data info.

IMPORTANT: Do not use hyphens (-) in your response.\n` + contextStr
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
              const res = await visionMcpClient.callTool({ name: "locate_object", arguments: args });
              toolResultText = res.content[0].text;
              logToolCall(question, "locate_object", args, "Success");
              
              // Parse progress details for premium UI status updates
              try {
                const parsed = JSON.parse(toolResultText);
                if (parsed.status && parsed.status.startsWith("SUCCESS")) {
                  sendProgress(`Success! YOLO localized "${args.target_name}". Directing robotic arm to move...`);
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
    log: toolCallLog.slice(-20) // Last 20 tool calls
  });
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
const wss = new WebSocket.Server({ server, path: '/wakeword' });

wss.on('connection', (clientWs) => {
  console.log('🔗 [WS Proxy] Flutter client connected to /wakeword proxy');

  // Connect to the Python wake word server
  let pythonWs;
  try {
    pythonWs = new WebSocket(WAKEWORD_WS_URL);
  } catch (e) {
    console.error('❌ [WS Proxy] Failed to create connection to Python wake word server:', e.message);
    clientWs.close();
    return;
  }

  pythonWs.on('open', () => {
    console.log('✅ [WS Proxy] Connected to Python wake word server on port 8003');
  });

  // Relay messages from Python → Flutter client
  pythonWs.on('message', (data) => {
    const msg = data.toString();
    console.log(`[WS PROXY] received from Python: ${msg}`);
    if (clientWs.readyState === WebSocket.OPEN) {
      clientWs.send(msg);
      console.log(`[WS PROXY] forwarded to Flutter: ${msg}`);
    }
  });

  // Relay messages from Flutter client → Python
  clientWs.on('message', (data) => {
    const msg = data.toString();
    console.log(`[NODE] received from Flutter: ${msg}`);
    if (pythonWs.readyState === WebSocket.OPEN) {
      pythonWs.send(msg);
      console.log(`[NODE] forwarded to Python: ${msg}`);
    } else {
      console.warn(`[NODE] cannot forward to Python (state=${pythonWs.readyState}): ${msg}`);
    }
  });

  pythonWs.on('error', (err) => {
    console.error('❌ [WS Proxy] Python WS error:', err.message);
  });

  pythonWs.on('close', () => {
    console.log('⚠️ [WS Proxy] Python WS closed');
    if (clientWs.readyState === WebSocket.OPEN) clientWs.close();
  });

  clientWs.on('close', () => {
    console.log('⚠️ [WS Proxy] Flutter client disconnected');
    if (pythonWs.readyState === WebSocket.OPEN) pythonWs.close();
  });

  clientWs.on('error', (err) => {
    console.error('❌ [WS Proxy] Client WS error:', err.message);
  });
});

server.listen(port, async () => {
  console.log(`🚀 Server running at http://localhost:${port}`);
  console.log(`🔊 Wake word WebSocket proxy available at ws://localhost:${port}/wakeword`);
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
