import 'dart:async';
import 'dart:convert';
import 'dart:html' as html;
import 'dart:typed_data'; // Used for Audio playback on Web

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';
import 'package:record/record.dart';
import 'package:video_player/video_player.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

class ChatMessage {
  final String text;
  final bool isUser;
  final bool isTyping;
  ChatMessage({required this.text, required this.isUser, this.isTyping = false});
}


enum AvatarState { idle, thinking, talking }

class ChatbotScreen extends StatefulWidget {
  const ChatbotScreen({super.key});

  @override
  State<ChatbotScreen> createState() => _ChatbotScreenState();
}

class _ChatbotScreenState extends State<ChatbotScreen> {
  final _audioRecorder = AudioRecorder();
  late VideoPlayerController _videoController;
  bool _isVideoInitialized = false;
  
  html.AudioElement? _audioElement;
  bool _isTtsInProgress = false;
  
  final TextEditingController _textController = TextEditingController();
  final ScrollController _scrollController = ScrollController();
  
  String _answeringEmoji = "😊";
  String _idleEmoji = "🤖";
  String _currentPersona = "john";
  String get _pName => _currentPersona == "linda" ? "Linda" : "John";
  AvatarState _currentAvatarState = AvatarState.talking;

  final List<ChatMessage> _messages = [
    ChatMessage(text: "John(🤖): Welcome! I'm John, your robotic assistant. How can I assist you today?", isUser: false)
  ];
  
  bool _isHandsFreeMode = false;
  bool _isHandsFreeRecordingCommand = false;
  final List<int> _commandBuffer = [];
  WebSocketChannel? _wakeWordChannel;
  StreamSubscription? _audioStreamSub;
  bool _wakeWordCooldown = false;
  bool _isListening = false;
  bool _isMenuVisible = false;
  String? _currentSubtitleText;

  // On Web: use '' (relative URL) so it always calls back the same server it was loaded from.
  // This means it works on localhost, ngrok, or any other host automatically.
  String get baseUrl {
    if (kIsWeb) return ''; // Relative URL - works with ngrok, localhost, any host
    return 'http://localhost:3000';
  }

  static const bool kIsWeb = identical(0, 0.0);

  @override
  void initState() {
    super.initState();
    _initVideo();
    
    // Connect to Python Wake Word Server
    try {
      _wakeWordChannel = WebSocketChannel.connect(Uri.parse('ws://localhost:8003'));
      _wakeWordChannel!.sink.add(json.encode({'action': 'set_persona', 'persona': _currentPersona}));
      
      _wakeWordChannel!.stream.listen((message) {
        final data = json.decode(message);
        if (data['event'] == 'WAKE_WORD_DETECTED' && !_isHandsFreeRecordingCommand && !_wakeWordCooldown && !_isTtsInProgress) {
          debugPrint("✅ [WAKE WORD] Heard '${data['model']}'");
          _playBeep();
          setState(() {
            _isHandsFreeMode = true; // Auto-enable hands-free if we heard wake word
          });
          _listen();
          
          // Auto-stop listening after 3.5 seconds
          Future.delayed(const Duration(milliseconds: 3500), () {
             if (_isListening && _isHandsFreeMode && _isHandsFreeRecordingCommand) {
                _listen(); // Call again to stop
             }
          });
        }
      });
    } catch (e) {
      debugPrint("Wake word server not available: $e");
    }
    _initializeAll();
  }

  Future<void> _initializeAll() async {
    await _fetchEmojis();
    await _initRecorder();
    await _initVideo();
    _speak("Welcome! I'm $_pName, your robotic assistant. How can I help you today?");
  }

  Future<void> _fetchEmojis() async {
    try {
      final res = await http.get(Uri.parse('$baseUrl/status-emojis'));
      if (res.statusCode == 200) {
        final data = json.decode(res.body);
        if (mounted) {
          setState(() {
            _answeringEmoji = data['answering'] ?? "😊";
            _idleEmoji = data['idle'] ?? "🤖";
            if (_messages.length == 1 && _messages[0].text.startsWith("John(")) {
              _messages[0] = ChatMessage(
                text: "$_pName($_idleEmoji): Welcome! I'm $_pName, your robotic assistant. How can I assist you today?",
                isUser: false
              );
            }
          });
        }
      }
    } catch (e) {
      debugPrint("❌ Failed to bind emojis from MCP server: $e");
    }
  }

  String _getAssetPathForState(AvatarState state, String persona) {
    if (persona == "linda") {
      switch (state) {
        case AvatarState.idle: return 'assets/lindaidle.mp4';
        case AvatarState.thinking: return 'assets/lindathinking.mp4';
        case AvatarState.talking: return 'assets/linda_talking.mp4';
      }
    } else {
      switch (state) {
        case AvatarState.idle: return 'assets/johnidle.mp4';
        case AvatarState.thinking: return 'assets/johnthinking.mp4';
        case AvatarState.talking: return 'assets/john_talking.mp4';
      }
    }
  }

  Future<void> _initVideo() async {
    String assetPath = _getAssetPathForState(_currentAvatarState, _currentPersona);
    _videoController = VideoPlayerController.asset(assetPath);
    try {
      await _videoController.initialize();
      await _videoController.setVolume(0); // MUTE
      await _videoController.setLooping(true);
      _videoController.play();
      setState(() {
        _isVideoInitialized = true;
      });
    } catch (e) {
      debugPrint("Video init error: $e");
    }
  }

  Future<void> _setAvatarState(AvatarState newState) async {
    if (!mounted) return;
    if (_currentAvatarState == newState && _videoController.dataSource == _getAssetPathForState(newState, _currentPersona)) {
      return; 
    }
    
    _currentAvatarState = newState;
    final oldController = _videoController;
    
    String assetPath = _getAssetPathForState(_currentAvatarState, _currentPersona);
    _videoController = VideoPlayerController.asset(assetPath);
    try {
      await _videoController.initialize();
      await _videoController.setVolume(0); // MUTE
      await _videoController.setLooping(true);
      _videoController.play();
      
      if (mounted) setState(() {}); 
      
      Future.delayed(const Duration(milliseconds: 300), () => oldController.dispose());
    } catch (e) {
      debugPrint("Video state swap error: $e");
    }
  }

  Future<void> _speak(String text) async {
    if (text.isEmpty) return;
    debugPrint('📢 John is speaking: $text');

    setState(() {
      _isTtsInProgress = true;
    });

    try {
      final url = '$baseUrl/tts';
      _audioElement?.pause();
      
      final response = await http.post(
        Uri.parse(url),
        headers: {'Content-Type': 'application/json'},
        body: json.encode({'text': text, 'persona': _currentPersona}),
      );

      if (response.statusCode == 200) {
        final contentType = response.headers['content-type'] ?? 'audio/mpeg';
        final blob = html.Blob([response.bodyBytes], contentType);
        final blobUrl = html.Url.createObjectUrlFromBlob(blob);
        _audioElement = html.AudioElement(blobUrl);
        _audioElement!.onEnded.listen((_) {
          if (mounted && _isTtsInProgress) {
            setState(() {
              _isTtsInProgress = false;
              _currentSubtitleText = null;
              if (_messages.isNotEmpty && _messages.last.isTyping) {
                _messages[_messages.length - 1] = ChatMessage(text: "$_pName($_idleEmoji): Please ask a question", isUser: false);
              }
            });
            _setAvatarState(AvatarState.idle);
          }
          html.Url.revokeObjectUrl(blobUrl);
        });

        if (!mounted || !_isTtsInProgress) {
           debugPrint('⛔ TTS audio downloaded, but user already pressed STOP.');
           html.Url.revokeObjectUrl(blobUrl);
           return;
        }

        _audioElement!.play();
        _setAvatarState(AvatarState.talking);
      } else {
        debugPrint('❌ TTS Server Error: ${response.statusCode}');
        if (mounted) {
          setState(() {
            _isTtsInProgress = false;
          });
        }
      }
    } catch (e) {
      debugPrint('❌ TTS Exception: $e');
      if (mounted) {
        setState(() {
          _isTtsInProgress = false;
        });
      }
    }
  }

  Future<void> _initRecorder() async {
    try {
      if (await _audioRecorder.hasPermission()) {
        debugPrint('Microphone permission granted.');
      } else {
        debugPrint('Microphone permission denied.');
      }
    } catch (e) {
      debugPrint('Recorder Init Exception: $e');
    }
  }

  @override
  void dispose() {
    _audioRecorder.dispose();
    _videoController.dispose();
    _textController.dispose();
    _scrollController.dispose();
    _audioElement?.pause();
    _audioElement = null;
    super.dispose();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
      }
    });
  }

  Future<void> _stopSpeaking() async {
    _audioElement?.pause();
    _audioElement?.removeAttribute('src'); 
    _setAvatarState(AvatarState.idle);
    
    if (mounted) {
      setState(() {
        _isTtsInProgress = false;
        _currentSubtitleText = null;
        if (_messages.isNotEmpty && _messages.last.isTyping) {
          _messages[_messages.length - 1] = ChatMessage(text: "$_pName($_idleEmoji): Please ask a question", isUser: false);
        }
      });
    }
  }

  Future<void> _sendMessage(String text) async {
    if (text.trim().isEmpty) return;
    
    _setAvatarState(AvatarState.thinking);

    setState(() {
      _messages.add(ChatMessage(text: text, isUser: true));
    });
    _textController.clear();
    _scrollToBottom();

    try {
      final response = await http.post(
        Uri.parse('$baseUrl/ask-gpt'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'question': text}),
      ).timeout(const Duration(seconds: 10));

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        if (data['success'] == true) {
          final answer = data['answer'];
          final newPersona = data['persona'];
          if (newPersona != null && newPersona != _currentPersona) {
             _currentPersona = newPersona;
             _setAvatarState(_currentAvatarState);
             
             if (_wakeWordChannel != null) {
               _wakeWordChannel!.sink.add(json.encode({'action': 'set_persona', 'persona': newPersona}));
             }
          }
          setState(() {
            _messages.add(ChatMessage(text: "$_pName($_answeringEmoji): Answering...", isUser: false));
          });
          _speak(answer);
        } else {
          throw Exception(data['message'] ?? 'Unknown error');
        }
      } else {
        throw Exception('Server error: ${response.statusCode}');
      }
    } catch (e) {
      debugPrint('Error sending message: $e');
      final errorMsg = "Oops! I couldn't process that right now. Don't worry, please try again, I'm ready to help!";
      setState(() {
        _messages.add(ChatMessage(text: "$_pName(❌): $errorMsg", isUser: false));
      });
      _speak(errorMsg);
    } finally {
      _scrollToBottom();
    }
  }

  Uint8List _createWavHeader(int sampleRate, int numChannels, int bitDepth, int dataSize) {
    var byteData = ByteData(44);
    byteData.setUint32(0, 0x52494646, Endian.big); // "RIFF"
    byteData.setUint32(4, 36 + dataSize, Endian.little);
    byteData.setUint32(8, 0x57415645, Endian.big); // "WAVE"
    byteData.setUint32(12, 0x666D7420, Endian.big); // "fmt "
    byteData.setUint32(16, 16, Endian.little); // chunk size
    byteData.setUint16(20, 1, Endian.little); // PCM format
    byteData.setUint16(22, numChannels, Endian.little);
    byteData.setUint32(24, sampleRate, Endian.little);
    byteData.setUint32(28, sampleRate * numChannels * (bitDepth ~/ 8), Endian.little);
    byteData.setUint16(32, numChannels * (bitDepth ~/ 8), Endian.little);
    byteData.setUint16(34, bitDepth, Endian.little);
    byteData.setUint32(36, 0x64617461, Endian.big); // "data"
    byteData.setUint32(40, dataSize, Endian.little);
    return byteData.buffer.asUint8List();
  }

  void _playBeep() {
    debugPrint('BEEP!');
  }

  Future<void> _listen() async {
    if (_isTtsInProgress) return;

    if (!_isListening) {
      if (!await _audioRecorder.hasPermission()) return;

      setState(() {
        _isListening = true;
        _textController.text = "Listening...";
      });

      const config = RecordConfig(
        encoder: AudioEncoder.wav,
        sampleRate: 44100,
        bitRate: 128000,
        numChannels: 1,
      );
      await _audioRecorder.start(config, path: '');
    } else {
      _textController.text = "Transcribing...";
      _setAvatarState(AvatarState.thinking);
      if (mounted) setState(() { _isListening = false; });

      // 400ms audio-pad so trailing syllables don't get abruptly cut off.
      await Future.delayed(const Duration(milliseconds: 400));
      final path = await _audioRecorder.stop();
      if (path == null) {
        setState(() {
          _textController.clear();
        });
        return;
      }

      try {
        final audioBytes = await http.readBytes(Uri.parse(path));
        
        var request = http.MultipartRequest('POST', Uri.parse('$baseUrl/transcribe'));
        request.files.add(http.MultipartFile.fromBytes(
          'audio', 
          audioBytes,
          filename: 'audio.wav',
          contentType: MediaType('audio', 'wav'),
        ));

        var response = await request.send();
        if (response.statusCode == 200) {
          var resBody = await response.stream.bytesToString();
          var data = json.decode(resBody);
          if (data['success'] == true && data['text'] != null) {
            final recognizedText = data['text'];
            _textController.clear();
            _sendMessage(recognizedText);
          } else {
            throw Exception('Transcription empty');
          }
        } else {
          throw Exception('Transcription failed: ${response.statusCode}');
        }
      } catch (e) {
        debugPrint('Transcription Error: $e');
        setState(() {
          _messages.add(ChatMessage(text: "System Error: Transcription failed.", isUser: false));
          _textController.clear();
        });
        _setAvatarState(AvatarState.idle);
      }
    }
  }

  Future<void> _uploadPdf() async {
    FilePickerResult? result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['pdf'],
      withData: true,
    );

    if (result != null && result.files.single.bytes != null) {
      final bytes = result.files.single.bytes!;
      final filename = result.files.single.name;
      
      setState(() {
        _messages.add(ChatMessage(text: "System: Uploading PDF...", isUser: false));
      });

      try {
        var request = http.MultipartRequest('POST', Uri.parse('$baseUrl/upload-pdf'));
        request.files.add(http.MultipartFile.fromBytes(
          'pdf', 
          bytes,
          filename: filename,
          contentType: MediaType('application', 'pdf'),
        ));

        var response = await request.send();
        if (response.statusCode == 200) {
          setState(() {
            _messages.add(ChatMessage(text: "System: PDF uploaded successfully!", isUser: false));
          });
        } else {
          throw Exception('Upload failed: ${response.statusCode}');
        }
      } catch (e) {
        debugPrint('Upload Error: $e');
        setState(() {
          _messages.add(ChatMessage(text: "System Error: PDF upload failed.", isUser: false));
        });
      }
    }
  }

  Widget _buildTopLogo() {
    return Positioned(
      top: 20,
      right: 20,
      child: SafeArea(
        child: Image.asset('assets/singaporepoly.png', height: 40),
      ),
    );
  }

  Widget _buildBackButton() {
    return Positioned(
      top: 20,
      left: 20,
      child: SafeArea(
        child: ElevatedButton.icon(
          onPressed: () => Navigator.pop(context),
          icon: const Icon(Icons.arrow_back, size: 16),
          label: const Text("Back"),
          style: ElevatedButton.styleFrom(
            backgroundColor: Colors.grey[600],
            foregroundColor: Colors.white,
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
          ),
        ),
      ),
    );
  }
  
  Widget _buildPresetQuestion(String question) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: OutlinedButton(
        onPressed: () => _sendMessage(question),
        style: OutlinedButton.styleFrom(
          side: const BorderSide(color: Colors.green, width: 1.5),
          backgroundColor: Colors.blue[50]?.withValues(alpha: 0.8),
          padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 20),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(25)),
          minimumSize: const Size(double.infinity, 40),
        ),
        child: Text(
          question,
          style: const TextStyle(color: Colors.black87),
        ),
      ),
    );
  }

  Widget _buildExpandedChatInterface() {
    return Container(
      padding: const EdgeInsets.all(20),
      height: 400, // Fixed height so the listview can scroll
      decoration: BoxDecoration(
        color: Colors.grey[850]?.withValues(alpha: 0.8),
        borderRadius: BorderRadius.circular(15),
      ),
      child: Column(
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            const Text("Chat History", style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 18)),
            Row(
              children: [
                const Text("Hands-Free Mode", style: TextStyle(color: Colors.white70)),
                Switch(
                  value: _isHandsFreeMode,
                  onChanged: (val) {
                    setState(() => _isHandsFreeMode = val);
                  },
                  activeColor: Colors.green,
                ),
              ],
            )
          ],
        ),
        const Divider(color: Colors.white24),
        Expanded(
          child: ListView.builder(
            controller: _scrollController,
            itemCount: _messages.length,
            itemBuilder: (context, index) {
              final msg = _messages[index];
              return Align(
                alignment: msg.isUser ? Alignment.centerRight : Alignment.centerLeft,
                child: Container(
                  margin: const EdgeInsets.symmetric(vertical: 5),
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: msg.isUser
                        ? Colors.green.withValues(alpha: 0.8)
                        : Colors.white.withValues(alpha: 0.1),
                    borderRadius: BorderRadius.circular(15),
                    border: msg.isUser
                        ? null
                        : Border.all(color: Colors.white.withValues(alpha: 0.2)),
                  ),
                  child: msg.isTyping
                      ? Text("$_pName($_idleEmoji): Typing...", style: const TextStyle(color: Colors.white, fontSize: 15))
                      : SelectableText(
                          msg.text,
                          style: const TextStyle(color: Colors.white, fontSize: 15),
                        ),
                ),
              );
            },
          ),
        ),
        const SizedBox(height: 15),
        
        Row(
          children: [
            Expanded(
              child: TextField(
                controller: _textController,
                decoration: InputDecoration(
                  hintText: "Type a question...",
                  hintStyle: const TextStyle(color: Colors.green),
                  filled: true,
                  fillColor: Colors.white.withValues(alpha: 0.9),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(25),
                    borderSide: const BorderSide(color: Colors.grey),
                  ),
                  contentPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
                ),
                onSubmitted: _sendMessage,
              ),
            ),
            const SizedBox(width: 10),
            ElevatedButton(
              onPressed: () => _sendMessage(_textController.text),
              style: ElevatedButton.styleFrom(
                backgroundColor: Colors.green, 
                foregroundColor: Colors.white,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(25)),
                padding: const EdgeInsets.symmetric(horizontal: 25, vertical: 15),
              ),
              child: const Text("Send"),
            ),
            const SizedBox(width: 10),
            GestureDetector(
              onTap: _listen,
              child: CircleAvatar(
                radius: 24,
                backgroundColor: _isListening ? Colors.red : Colors.blue,
                child: const Icon(Icons.mic, color: Colors.white),
              ),
            ),
            const SizedBox(width: 10),
            ElevatedButton(
              onPressed: _stopSpeaking,
              style: ElevatedButton.styleFrom(
                backgroundColor: Colors.amber, 
                foregroundColor: Colors.white,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(25)),
                padding: const EdgeInsets.symmetric(horizontal: 25, vertical: 15),
              ),
              child: const Text("Stop"),
            ),
            const SizedBox(width: 10),
            ElevatedButton(
              onPressed: () => setState(() => _messages.clear()),
              style: ElevatedButton.styleFrom(
                backgroundColor: Colors.redAccent, 
                foregroundColor: Colors.white,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(25)),
                padding: const EdgeInsets.symmetric(horizontal: 25, vertical: 15),
              ),
              child: const Text("Clear"),
            ),
          ],
        ),
      ],
    ),
    );
  }

  Widget _buildAvatar() {
    if (!_isVideoInitialized) {
      return Container(
        color: Colors.white,
        child: const Center(
          child: CircularProgressIndicator(color: Colors.green),
        ),
      );
    }

    double scale = (_currentAvatarState == AvatarState.idle || _currentAvatarState == AvatarState.thinking) ? 0.8 : 1.0;

    return Container(
      color: Colors.white,
      width: double.infinity,
      height: double.infinity,
      child: Center(
        child: Transform.scale(
          scale: scale,
          child: FittedBox(
            fit: BoxFit.cover,
            child: SizedBox(
              width: _videoController.value.size.width,
              height: _videoController.value.size.height,
              child: VideoPlayer(_videoController),
            ),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      body: Stack(
        children: [
          Positioned.fill(
            child: _buildAvatar(),
          ),
          
          _buildTopLogo(),
          _buildBackButton(),

          if (_isMenuVisible)
            Positioned(
              bottom: 100,
              left: 20,
              right: 20,
              child: _buildExpandedChatInterface(),
            ),

          if (!_isMenuVisible && _messages.isNotEmpty)
            Positioned(
              bottom: 150,
              left: 100,
              right: 100,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 30),
                decoration: BoxDecoration(
                  color: Colors.grey[900]?.withValues(alpha: 0.8), 
                  borderRadius: BorderRadius.circular(15),
                  border: Border.all(color: Colors.white24),
                ),
                child: Row(
                  children: [
                    GestureDetector(
                      onTap: _listen,
                      child: CircleAvatar(
                        radius: 25,
                        backgroundColor: _isListening ? Colors.red : Colors.blue,
                        child: Icon(Icons.mic, color: Colors.white, size: 30),
                      ),
                    ),
                    const SizedBox(width: 20),
                    Expanded(
                      child: Text(
                        _messages.last.text,
                        style: const TextStyle(color: Colors.white, fontSize: 16),
                        textAlign: TextAlign.center,
                      ),
                    ),
                  ],
                ),
              ),
            ),

          // Persona Switch FAB — bottom left
          Positioned(
            bottom: 30,
            left: 20,
            child: GestureDetector(
              onTap: () async {
                if (_isTtsInProgress) return;
                final newPersona = _currentPersona == "john" ? "linda" : "john";
                setState(() => _currentPersona = newPersona);
                await _setAvatarState(_currentAvatarState);

                // Notify backend so voice (TTS) matches the new persona
                await http.post(
                  Uri.parse('$baseUrl/switch-persona'),
                  headers: {'Content-Type': 'application/json'},
                  body: jsonEncode({'persona': newPersona}),
                );

                final greeting = newPersona == "linda"
                    ? "Hi! I'm Linda, your robotic assistant. How can I help you today?"
                    : "Hey! I'm John, your robotic assistant. What can I do for you?";
                setState(() {
                  _messages.add(ChatMessage(
                    text: "$_pName($_idleEmoji): $greeting",
                    isUser: false,
                  ));
                });
                _scrollToBottom();
                _speak(greeting);
              },
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 300),
                padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 12),
                decoration: BoxDecoration(
                  color: _currentPersona == "linda"
                      ? Colors.pink[600]
                      : Colors.blue[700],
                  borderRadius: BorderRadius.circular(30),
                  boxShadow: [
                    BoxShadow(
                      color: (_currentPersona == "linda" ? Colors.pink : Colors.blue)
                          .withValues(alpha: 0.5),
                      blurRadius: 12,
                      spreadRadius: 2,
                    ),
                  ],
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      _currentPersona == "linda" ? Icons.female : Icons.male,
                      color: Colors.white,
                      size: 24,
                    ),
                    const SizedBox(width: 8),
                    Text(
                      _currentPersona == "linda" ? "Linda ♀" : "John ♂",
                      style: const TextStyle(
                        color: Colors.white,
                        fontWeight: FontWeight.bold,
                        fontSize: 16,
                      ),
                    ),
                    const SizedBox(width: 8),
                    const Icon(Icons.swap_horiz, color: Colors.white70, size: 18),
                  ],
                ),
              ),
            ),
          ),

          // Menu button — bottom right
          Positioned(
            bottom: 30,
            right: 20,
            child: Row(
              children: [
                if (_isMenuVisible)
                  ElevatedButton(
                    onPressed: _uploadPdf,
                    style: ElevatedButton.styleFrom(
                      backgroundColor: Colors.grey[700],
                      foregroundColor: Colors.white,
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
                      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
                    ),
                    child: const Text("Upload PDF"),
                  ),
                const SizedBox(width: 10),
                ElevatedButton.icon(
                  onPressed: () {
                    setState(() => _isMenuVisible = !_isMenuVisible);
                  },
                  icon: const Icon(Icons.menu, size: 18),
                  label: Text(_isMenuVisible ? "Hide Menu" : "Menu"),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.grey[700],
                    foregroundColor: Colors.white,
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
                    padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
                  ),
                ),
              ],
            ),
          ),

        ],
      ),
    );
  }
}
