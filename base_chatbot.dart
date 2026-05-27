import 'dart:async';
import 'dart:convert';
import 'dart:html' as html; // Used for Audio playback on Web

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';
import 'package:record/record.dart';
import 'package:video_player/video_player.dart';

class ChatMessage {
  final String text;
  final bool isUser;
  ChatMessage({required this.text, required this.isUser});
}


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
  
  String _answeringEmoji = "≡ƒÿè";
  String _idleEmoji = "≡ƒñû";
  String _currentPersona = "john";
  String get _pName => _currentPersona == "linda" ? "Linda" : "John";

  final List<ChatMessage> _messages = [
    ChatMessage(text: "John(≡ƒñû): Welcome! I'm John, your robotic assistant. How can I assist you today?", isUser: false)
  ];
  
  bool _isListening = false;
  bool _isMenuVisible = false;

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
            _answeringEmoji = data['answering'] ?? "≡ƒÿè";
            _idleEmoji = data['idle'] ?? "≡ƒñû";
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
      debugPrint("Γ¥î Failed to bind emojis from MCP server: $e");
    }
  }

  Future<void> _initVideo() async {
    String assetPath = _currentPersona == "linda" ? 'assets/video3_720p.mp4' : 'assets/video4_720p.mp4';
    _videoController = VideoPlayerController.asset(assetPath);
    try {
      await _videoController.initialize();
      await _videoController.setVolume(0); // MUTE OLD COMMERCIAL LOOP!
      await _videoController.setLooping(true); // Loop while we talk
      setState(() {
        _isVideoInitialized = true;
      });
    } catch (e) {
      debugPrint("Video init error: $e");
    }
  }

  Future<void> _switchVideoMode() async {
    final oldController = _videoController;
    String assetPath = _currentPersona == "linda" ? 'assets/video3_720p.mp4' : 'assets/video4_720p.mp4';
    _videoController = VideoPlayerController.asset(assetPath);
    try {
      await _videoController.initialize();
      await _videoController.setVolume(0); // MUTE OLD COMMERCIAL LOOP!
      await _videoController.setLooping(true);
      setState(() {}); // Trigger rebuild to show new controller
      // clean up old
      Future.delayed(const Duration(milliseconds: 200), () => oldController.dispose());
    } catch (e) {
      debugPrint("Video swap error: $e");
    }
  }

  Future<void> _speak(String text) async {
    if (text.isEmpty) return;
    debugPrint('≡ƒôó John is speaking: $text');

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
          if (mounted) {
            setState(() {
              _isTtsInProgress = false;
              // Reset video
              _videoController.pause();
              _videoController.seekTo(Duration.zero);
              
              // Update status message when speaking finishes
              if (_messages.isNotEmpty && _messages.last.text.contains("($_answeringEmoji): Answering...")) {
                _messages[_messages.length - 1] = ChatMessage(text: "$_pName($_idleEmoji): Please ask a question", isUser: false);
              }
            });
          }
          html.Url.revokeObjectUrl(blobUrl);
        });

        _audioElement!.onError.listen((e) {
          debugPrint('Γ¥î Audio error: $e');
          if (mounted) {
            setState(() {
              _isTtsInProgress = false;
            });
          }
          html.Url.revokeObjectUrl(blobUrl);
        });

        _videoController.setLooping(true);
        _videoController.play();
        await _audioElement!.play();
      } else {
        debugPrint('Γ¥î TTS Server Error: ${response.statusCode}');
        if (mounted) {
          setState(() {
            _isTtsInProgress = false;
          });
        }
      }
    } catch (e) {
      debugPrint('Γ¥î TTS Exception: $e');
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
    _videoController.pause();
    _videoController.seekTo(Duration.zero);
    
    if (mounted) {
      setState(() {
        _isTtsInProgress = false;
        // Update status if manually stopped too
        if (_messages.isNotEmpty && _messages.last.text.contains("($_answeringEmoji): Answering...")) {
          _messages[_messages.length - 1] = ChatMessage(text: "$_pName($_idleEmoji): Please ask a question", isUser: false);
        }
      });
    }
  }

  Future<void> _sendMessage(String text) async {
    if (text.trim().isEmpty) return;

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
             await _switchVideoMode();
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
        _messages.add(ChatMessage(text: "$_pName(Γ¥î): $errorMsg", isUser: false));
      });
      _speak(errorMsg);
    } finally {
      _scrollToBottom();
    }
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
      setState(() {
        _isListening = false;
        _textController.text = "Transcribing...";
      });

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
      decoration: BoxDecoration(
        color: Colors.grey[850]?.withValues(alpha: 0.8),
        borderRadius: BorderRadius.circular(15),
      ),
      child: Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (_messages.isNotEmpty)
          Text(
            _messages.last.text,
            style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 16),
            textAlign: TextAlign.center,
          ),
        const SizedBox(height: 15),
        const Text(
          "Get started with these questions:",
          style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white70),
        ),
        const SizedBox(height: 10),
        _buildPresetQuestion("How do I instruct the robotic arm to pick up a screwdriver?"),
        _buildPresetQuestion("What are the payload limits of this robotic arm?"),
        _buildPresetQuestion("Can you explain the calibration process for the arm?"),
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

    return Container(
      color: Colors.white,
      width: double.infinity,
      height: double.infinity,
      child: FittedBox(
        fit: BoxFit.cover,
        child: SizedBox(
          width: _videoController.value.size.width,
          height: _videoController.value.size.height,
          child: VideoPlayer(_videoController),
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

          // Persona Switch FAB ΓÇö bottom left
          Positioned(
            bottom: 30,
            left: 20,
            child: GestureDetector(
              onTap: () async {
                if (_isTtsInProgress) return;
                final newPersona = _currentPersona == "john" ? "linda" : "john";
                setState(() => _currentPersona = newPersona);
                await _switchVideoMode();

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
                      _currentPersona == "linda" ? "Linda ΓÖÇ" : "John ΓÖé",
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

          // Menu button ΓÇö bottom right
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
