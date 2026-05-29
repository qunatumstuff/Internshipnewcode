import 'dart:async';
import 'dart:convert';
import 'dart:html' as html;
import 'dart:math' as math;
import 'dart:typed_data';
import 'dart:web_audio' as wa;

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';
import 'package:record/record.dart';
import 'package:video_player/video_player.dart';

// ─── Data ────────────────────────────────────────────────────────────────────

enum AvatarState { idle, thinking, talking }

class ChatMessage {
  final String text;
  final bool isUser;
  ChatMessage({required this.text, required this.isUser});
}

// ─── Widget ───────────────────────────────────────────────────────────────────

class ChatbotScreen extends StatefulWidget {
  const ChatbotScreen({super.key});

  @override
  State<ChatbotScreen> createState() => _ChatbotScreenState();
}

class _ChatbotScreenState extends State<ChatbotScreen>
    with TickerProviderStateMixin {
  // ── Audio ──
  final _audioRecorder = AudioRecorder();
  html.AudioElement? _audioElement;
  bool _isTtsInProgress = false;
  bool _isCcEnabled = true;
  String? _currentSubtitleText;

  // ── Video ──
  VideoPlayerController? _videoController;
  final Map<String, VideoPlayerController> _cachedControllers = {};
  bool _isVideoInitialized = false;
  AvatarState _avatarState = AvatarState.talking; // start on talking
  String _loadedVideoPersona = 'john'; // tracks which persona the current video belongs to
  bool _isSwitchingVideo = false;
  AvatarState? _pendingAvatarState;
  bool _pendingForce = false;

  // ── UI ──
  final TextEditingController _textController = TextEditingController();
  final ScrollController _scrollController = ScrollController();
  final ScrollController _subtitleScrollController = ScrollController();
  bool _isListening = false;
  bool _isMenuVisible = false;

  // ── Hands-free / wake-word ──
  bool _isHandsFreeMode = false;
  html.WebSocket? _wakeWordSocket;
  String _wakeWsStatus = 'disconnected'; // connected | connecting | disconnected | error
  bool _wakeWsReconnecting = false;      // prevents overlapping reconnect attempts

  // ── VAD Silence Detection ──
  html.MediaStream? _vadStream;
  wa.AudioContext? _vadAudioContext;
  wa.AnalyserNode? _vadAnalyser;
  Timer? _vadTimer;

  // ── Persona ──
  String _currentPersona = 'john';
  String get _pName => _currentPersona == 'linda' ? 'Linda' : 'John';
  bool get _isInteractionBlocked =>
      _isTtsInProgress ||
      _avatarState == AvatarState.thinking ||
      (_messages.isNotEmpty && _messages.last.text == '__THINKING__');

  // ── Emojis ──
  String _answeringEmoji = '🤖';
  String _idleEmoji = '🤗';

  // ── Messages ──
  final List<ChatMessage> _messages = [];

  // ── Mic Recording Stream ──
  html.MediaRecorder? _webMediaRecorder;
  List<html.Blob> _webAudioChunks = [];

  // ── Mic Debug Logs ──
  final List<String> _micDebugLogs = [];

  void _addUiLog(String log) {
    debugPrint(log);
    if (mounted) {
      setState(() {
        _micDebugLogs.add(log);
        if (_micDebugLogs.length > 15) _micDebugLogs.removeAt(0); // keep last 15
      });
    }
  }

  // ── Visualizer animation ──
  late AnimationController _vizController;

  // ── URL ──
  static const bool kIsWeb = identical(0, 0.0);
  String get baseUrl => kIsWeb ? '' : 'http://localhost:3000';

  // ─────────────────────────────────────────────────────────────────────────
  //  Lifecycle
  // ─────────────────────────────────────────────────────────────────────────

  @override
  void initState() {
    super.initState();
    _vizController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 500),
    )..repeat(reverse: true);
    // _connectWakeWord(); // TEMPORARILY DISABLED: Testing manual mic button only
    _initializeAll();
  }

  @override
  void dispose() {
    _vizController.dispose();
    _audioRecorder.dispose();
    _videoController?.dispose();
    for (final controller in _cachedControllers.values) {
      controller.dispose();
    }
    _cachedControllers.clear();
    _textController.dispose();
    _scrollController.dispose();
    _subtitleScrollController.dispose();
    _audioElement?.pause();
    _audioElement = null;
    debugPrint('⚠️ [DEBUG] Calling _wakeWordSocket?.close() from dispose()');
    _wakeWordSocket?.close();
    super.dispose();
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Init
  // ─────────────────────────────────────────────────────────────────────────

  Future<void> _initializeAll() async {
    await _fetchEmojis();
    await _initRecorder();
    // Load talking video first so it's ready immediately
    await _loadVideo(AvatarState.talking);
    const welcome =
        "Welcome! I'm John, your robotic assistant. How can I help you today?";
    setState(() {
      _messages.add(ChatMessage(
          text: 'John($_idleEmoji): $welcome', isUser: false));
    });
    _speak(welcome);
  }

  Future<void> _fetchEmojis() async {
    try {
      final res = await http.get(Uri.parse('$baseUrl/status-emojis'));
      if (res.statusCode == 200) {
        final data = json.decode(res.body);
        if (mounted) {
          setState(() {
            _answeringEmoji = data['answering'] ?? '🤖';
            _idleEmoji = data['idle'] ?? '🤗';
          });
        }
      }
    } catch (e) {
      debugPrint('Emoji fetch failed: $e');
    }
  }

  Future<void> _initRecorder() async {
    try {
      await _audioRecorder.hasPermission();
    } catch (e) {
      debugPrint('Recorder init error: $e');
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Video state machine
  // ─────────────────────────────────────────────────────────────────────────

  String _assetFor(String persona, AvatarState state) {
    if (persona == 'linda') {
      switch (state) {
        case AvatarState.idle:
          return 'assets/lindaidle.mp4';
        case AvatarState.thinking:
          return 'assets/lindathinking.mp4';
        case AvatarState.talking:
          return 'assets/linda_talking.mp4';
      }
    } else {
      switch (state) {
        case AvatarState.idle:
          return 'assets/johnidle.mp4';
        case AvatarState.thinking:
          return 'assets/johnthinking.mp4';
        case AvatarState.talking:
          return 'assets/john_talking.mp4';
      }
    }
  }

  Future<void> _preInitPersonaVideos() async {
    final states = [AvatarState.idle, AvatarState.thinking, AvatarState.talking];
    for (final state in states) {
      final asset = _assetFor(_currentPersona, state);
      if (!_cachedControllers.containsKey(asset)) {
        debugPrint('🎬 [VIDEO] Pre-initializing $state for $_currentPersona -> $asset');
        final controller = VideoPlayerController.asset(asset);
        await controller.initialize();
        await controller.setVolume(0);
        await controller.setLooping(true);
        
        // Bulletproof fix for browser auto-pausing the video AND broken web looping
        controller.addListener(() {
          if (!mounted) return;
          final bool isAtEnd = controller.value.isInitialized && 
                               controller.value.duration > Duration.zero &&
                               controller.value.position >= controller.value.duration;
                               
          if (!controller.value.isPlaying && asset.contains(_currentPersona)) {
            if (isAtEnd) {
              controller.seekTo(Duration.zero);
            }
            controller.play().catchError((_) {});
          }
        });
        
        _cachedControllers[asset] = controller;
      }
    }
  }

  Future<void> _loadVideo(AvatarState state, {bool force = false}) async {
    if (force) {
      _pendingForce = true;
    }
    if (_isSwitchingVideo) {
      _pendingAvatarState = state;
      debugPrint('🎬 [VIDEO] Queued pending state: $state (switching in progress)');
      return;
    }
    final useForce = force || _pendingForce;
    _pendingForce = false;

    // Skip reload if same state, same persona, already initialized, and not forced
    if (_avatarState == state && _loadedVideoPersona == _currentPersona && _isVideoInitialized && !useForce) {
      debugPrint('🎬 [VIDEO] Skipping reload - already on $state for $_currentPersona');
      return;
    }
    _isSwitchingVideo = true;
    _pendingAvatarState = null;

    try {
      // Ensure all 3 videos for the active persona are fully initialized and cached!
      await _preInitPersonaVideos();

      final targetAsset = _assetFor(_currentPersona, state);
      final controller = _cachedControllers[targetAsset]!;

      // Keep all 3 videos for the active persona playing to prevent CanvasKit freeze
      final states = [AvatarState.idle, AvatarState.thinking, AvatarState.talking];
      for (final s in states) {
        final asset = _assetFor(_currentPersona, s);
        final c = _cachedControllers[asset];
        if (c != null && !c.value.isPlaying) {
          c.play();
        }
      }

      if (mounted) {
        setState(() {
          _videoController = controller;
          _avatarState = state;
          _loadedVideoPersona = _currentPersona;
          _isVideoInitialized = true;
        });
      }
      debugPrint('🎬 [VIDEO] ✅ Now playing: $state for $_currentPersona');

      // Pause controllers for the OTHER persona to save resources
      _cachedControllers.forEach((key, c) {
        if (!key.contains(_currentPersona)) {
          c.pause();
        }
      });
    } catch (e) {
      debugPrint('🎬 [VIDEO] ❌ Error loading ($state): $e');
    } finally {
      _isSwitchingVideo = false;
      // If a state switch request arrived while we were loading, handle it now
      if (_pendingAvatarState != null) {
        final nextState = _pendingAvatarState!;
        _pendingAvatarState = null;
        debugPrint('🎬 [VIDEO] Processing pending state: $nextState');
        await _loadVideo(nextState, force: _pendingForce);
      }
    }
  }

  Future<void> _setAvatarState(AvatarState state) async {
    // Always reload if persona changed
    final personaChanged = _loadedVideoPersona != _currentPersona;
    if (_avatarState == state && !personaChanged && _isVideoInitialized) return;
    await _loadVideo(state, force: personaChanged);
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  TTS / Stop
  // ─────────────────────────────────────────────────────────────────────────

  /// Send a mute/unmute command to the wake word server so it doesn't
  /// trigger on the robot's own voice.
  void _setWakeWordMute(bool muted) {
    if (_wakeWordSocket != null &&
        _wakeWordSocket!.readyState == html.WebSocket.OPEN) {
      _wakeWordSocket!.send(json.encode({
        'action': muted ? 'mute' : 'unmute',
      }));
      debugPrint('🔇 Wake-word mute=${muted}');
    }
  }

  Future<void> _speak(String text) async {
    if (text.isEmpty) return;
    _setWakeWordMute(true); // Mute wake word while speaking
    if (mounted) {
      setState(() {
        _isTtsInProgress = true;
        _currentSubtitleText = text;
      });
    }
    await _setAvatarState(AvatarState.talking);

    try {
      // Kill any previous audio/speech synthesis sessions
      _audioElement?.pause();
      _audioElement?.src = '';
      try {
        html.window.speechSynthesis?.cancel();
      } catch (_) {}

      final response = await http.post(
        Uri.parse('$baseUrl/tts'),
        headers: {'Content-Type': 'application/json'},
        body: json.encode({'text': text, 'persona': _currentPersona}),
      );

      if (response.statusCode == 200) {
        final contentType =
            response.headers['content-type'] ?? 'audio/mpeg';
        final blob = html.Blob([response.bodyBytes], contentType);
        final blobUrl = html.Url.createObjectUrlFromBlob(blob);

        final audio = html.AudioElement(blobUrl);
        _audioElement = audio;

        audio.onEnded.listen((_) async {
          if (_audioElement != audio) return; // Ignore events from old audio sessions
          html.Url.revokeObjectUrl(blobUrl);
          _setWakeWordMute(false); // Re-enable wake word
          if (mounted) {
            setState(() {
              _isTtsInProgress = false;
              _currentSubtitleText = null;
            });
          }
          await _setAvatarState(AvatarState.idle);
        });

        audio.onError.listen((_) {
          if (_audioElement != audio) return; // Ignore events from old audio sessions
          html.Url.revokeObjectUrl(blobUrl);
          debugPrint('⚠️ Audio playback encountered an error event.');
        });

        audio.play().then((_) {
          // The browser may auto-pause our muted videos when this new audio starts playing.
          // Force all active persona videos to play immediately!
          if (mounted) {
            final states = [AvatarState.idle, AvatarState.thinking, AvatarState.talking];
            for (final s in states) {
              final asset = _assetFor(_currentPersona, s);
              final c = _cachedControllers[asset];
              if (c != null && !c.value.isPlaying) {
                c.play();
              }
            }
          }
        }).catchError((playError) {
          if (_audioElement != audio) return;
          debugPrint('🔇 Autoplay warning caught or audio play failed: $playError. Falling back to Native TTS...');
          _speakNative(text);
        });
      } else {
        debugPrint('⚠️ Server TTS returned status ${response.statusCode}, falling back to Native TTS...');
        _speakNative(text);
      }
    } catch (e) {
      debugPrint('⚠️ TTS HTTP/Network error: $e, falling back to Native TTS...');
      _speakNative(text);
    }
  }

  /// Free Web Speech API local fallback (bypasses all backend audio key failures and crashes!)
  void _speakNative(String text) {
    try {
      final synth = html.window.speechSynthesis;
      if (synth == null) return;
      _setWakeWordMute(true); // Mute wake word while speaking

      // Cancel any ongoing speech
      synth.cancel();

      final utterance = html.SpeechSynthesisUtterance(text);
      
      // Set voice based on current persona
      final voices = synth.getVoices();
      html.SpeechSynthesisVoice? selectedVoice;
      for (var voice in voices) {
        final name = voice.name?.toLowerCase() ?? '';
        final lang = voice.lang?.toLowerCase() ?? '';
        if (_currentPersona == 'linda') {
          if (lang.contains('en') && (name.contains('female') || name.contains('google us english') || name.contains('zira') || name.contains('hazel') || name.contains('samantha'))) {
            selectedVoice = voice;
            break;
          }
        } else {
          if (lang.contains('en') && (name.contains('male') || name.contains('david') || name.contains('google uk english male') || name.contains('mark') || name.contains('microsoft david'))) {
            selectedVoice = voice;
            break;
          }
        }
      }
      if (selectedVoice != null) {
        utterance.voice = selectedVoice;
      }

      utterance.onStart.listen((_) {
        debugPrint('📢 [NATIVE TTS] Started speaking...');
        if (mounted) {
          setState(() {
            _isTtsInProgress = true;
            _currentSubtitleText = text;
          });
          // The browser may auto-pause our muted videos when this new audio starts playing.
          // Force all active persona videos to play immediately!
          final states = [AvatarState.idle, AvatarState.thinking, AvatarState.talking];
          for (final s in states) {
            final asset = _assetFor(_currentPersona, s);
            final c = _cachedControllers[asset];
            if (c != null && !c.value.isPlaying) {
              c.play();
            }
          }
        }
      });

      utterance.onEnd.listen((_) async {
        debugPrint('📢 [NATIVE TTS] Completed successfully.');
        _setWakeWordMute(false);
        if (mounted) {
          setState(() {
            _isTtsInProgress = false;
            _currentSubtitleText = null;
          });
        }
        await _setAvatarState(AvatarState.idle);
      });

      utterance.onError.listen((e) async {
        debugPrint('❌ [NATIVE TTS] Error: $e');
        _setWakeWordMute(false);
        if (mounted) {
          setState(() {
            _isTtsInProgress = false;
            _currentSubtitleText = null;
          });
        }
        await _setAvatarState(AvatarState.idle);
      });

      synth.speak(utterance);
    } catch (e) {
      debugPrint('❌ [NATIVE TTS] Exception: $e');
    }
  }

  Future<void> _stopSpeaking() async {
    // Completely kill audio
    _audioElement?.pause();
    _audioElement?.src = '';
    _audioElement = null;
    try {
      html.window.speechSynthesis?.cancel();
    } catch (_) {}
    if (mounted) {
      setState(() {
        _isTtsInProgress = false;
        _currentSubtitleText = null;
      });
    }
    await _setAvatarState(AvatarState.idle);
  }

  Future<void> _emergencyStop() async {
    // Kill audio
    _audioElement?.pause();
    _audioElement?.src = '';
    _audioElement = null;
    try {
      html.window.speechSynthesis?.cancel();
    } catch (_) {}

    // Stop recording
    if (_isListening) {
      try {
        await _audioRecorder.stop();
      } catch (_) {}
    }
    _stopSilenceDetection();

    // Reset wake word socket if connected
    if (_isHandsFreeMode) {
      _disconnectWakeWord();
      _isHandsFreeMode = false;
    }

    // Set avatar to idle
    await _setAvatarState(AvatarState.idle);

    if (mounted) {
      setState(() {
        _isListening = false;
        _isTtsInProgress = false;
        _currentSubtitleText = null;
        _textController.clear();
        _messages.add(ChatMessage(
            text: "⚠️ System: EMERGENCY STOP triggered! All operations halted.",
            isUser: false));
      });
    }
    _scrollToBottom();
  }

  void _clearChat() {
    setState(() {
      _messages.clear();
      final welcome =
          "Welcome! I'm John, your robotic assistant. How can I help you today?";
      _messages.add(ChatMessage(
          text: '$_pName($_idleEmoji): $welcome', isUser: false));
    });
    _scrollToBottom();
  }

  Widget _buildPresetQuestion(String label, String fullQuestion) {
    final bool blocked = _isInteractionBlocked;
    return Padding(
      padding: const EdgeInsets.only(right: 6, bottom: 6),
      child: GestureDetector(
        onTap: blocked ? () {} : () => _sendMessage(fullQuestion),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          decoration: BoxDecoration(
            color: blocked ? Colors.grey[300] : const Color(0xFFE0F7FA), // Light blue/teal tint matching screenshot
            borderRadius: BorderRadius.circular(20),
          ),
          child: Text(
            label,
            style: TextStyle(
              color: blocked ? Colors.black38 : Colors.black87,
              fontWeight: FontWeight.bold,
              fontSize: 12,
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildPillBtn(String label, IconData icon, Color color, VoidCallback onTap, {bool allowAlways = false}) {
    final bool blocked = _isInteractionBlocked && !allowAlways;
    return Padding(
      padding: const EdgeInsets.only(right: 6, bottom: 6),
      child: GestureDetector(
        onTap: blocked ? () {} : onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          decoration: BoxDecoration(
            color: blocked ? Colors.grey[400] : color,
            borderRadius: BorderRadius.circular(20),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, color: blocked ? Colors.black38 : Colors.white, size: 14),
              const SizedBox(width: 4),
              Text(
                label,
                style: TextStyle(
                  color: blocked ? Colors.black38 : Colors.white,
                  fontWeight: FontWeight.bold,
                  fontSize: 12,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Microphone / transcription
  // ─────────────────────────────────────────────────────────────────────────

  Future<void> _listen() async {
    _addUiLog('[MIC] Button pressed');
    // Do not allow listening while chatbot is speaking
    if (_isTtsInProgress) {
      _addUiLog('[MIC] Blocked: TTS is in progress');
      return;
    }
    _unlockAudio();

    if (!_isListening) {
      // Tell Python to release its mic before the browser opens its own
      _pauseWakeWord();

      _addUiLog('[MIC] Requesting browser microphone permission');
      html.MediaStream? stream;
      try {
        stream = await html.window.navigator.mediaDevices?.getUserMedia({'audio': true});
      } catch (e) {
        _addUiLog('[MIC] Permission denied or error: $e');
        _resumeWakeWord(); // Give mic back to Python if browser couldn't open it
        return;
      }
      if (stream == null) {
        _addUiLog('[MIC] Permission denied (stream null)');
        _resumeWakeWord();
        return;
      }
      _addUiLog('[MIC] Permission granted');

      setState(() {
        _isListening = true;
        _textController.text = 'Listening...';
      });

      String mimeType = '';
      if (html.MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
        mimeType = 'audio/webm;codecs=opus';
      } else if (html.MediaRecorder.isTypeSupported('audio/webm')) {
        mimeType = 'audio/webm';
      } else if (html.MediaRecorder.isTypeSupported('audio/ogg;codecs=opus')) {
        mimeType = 'audio/ogg;codecs=opus';
      } else {
        _addUiLog('[MIC] Warning: no standard webm/opus type supported. Using default.');
      }
      _addUiLog('[MIC] Selected MIME type: ${mimeType.isEmpty ? "default" : mimeType}');

      _webMediaRecorder = mimeType.isNotEmpty 
          ? html.MediaRecorder(stream, {'mimeType': mimeType})
          : html.MediaRecorder(stream);

      _webAudioChunks = [];

      _webMediaRecorder!.addEventListener('start', (e) {
        _addUiLog('[MIC] MediaRecorder state: ${_webMediaRecorder?.state}');
      });

      _webMediaRecorder!.addEventListener('pause', (e) {
        _addUiLog('[MIC] MediaRecorder state: ${_webMediaRecorder?.state}');
      });

      _webMediaRecorder!.addEventListener('dataavailable', (html.Event e) {
        final blobEvent = e as html.BlobEvent;
        final data = blobEvent.data;
        if (data != null) {
          _addUiLog('[MIC] ondataavailable: size=${data.size}, type=${data.type}');
          if (data.size > 0) {
            _webAudioChunks.add(data);
          }
        } else {
          _addUiLog('[MIC] ondataavailable: null data');
        }
      });

      _webMediaRecorder!.addEventListener('stop', (e) async {
        _addUiLog('[FLUTTER] recording stopped');
        _addUiLog('[MIC] chunk count: ${_webAudioChunks.length}');
        
        final sizes = _webAudioChunks.map((b) => b.size).join(',');
        _addUiLog('[MIC] chunk sizes: $sizes');
        
        if (_webAudioChunks.isEmpty) {
          _addUiLog('[MIC] audio bytes are empty. Aborting.');
          if (mounted) setState(() => _textController.clear());
          await _setAvatarState(AvatarState.idle);
          _resumeWakeWord();
          return;
        }

        final blob = html.Blob(_webAudioChunks, mimeType.isEmpty ? 'audio/webm' : mimeType);
        _addUiLog('[MIC] final blob size: ${blob.size}');

        final reader = html.FileReader();
        reader.readAsArrayBuffer(blob);
        await reader.onLoadEnd.first; // wait for read to complete
        final Uint8List audioBytes = reader.result as Uint8List;

        _addUiLog('[MIC] Audio blob/file size: ${audioBytes.length} bytes');

        if (audioBytes.length < 5000) {
          _sendMessage('System Error: Recorded audio file is suspiciously small (${audioBytes.length} bytes).');
          await _setAvatarState(AvatarState.idle);
          _resumeWakeWord();
          return;
        }

        final uploadUrl = '$baseUrl/transcribe';
        _addUiLog('[MIC] Uploading to: $uploadUrl');
        final req = http.MultipartRequest('POST', Uri.parse(uploadUrl));
        req.files.add(http.MultipartFile.fromBytes(
          'audio',
          audioBytes,
          filename: 'audio.webm',
          contentType: MediaType('audio', 'webm'),
        ));

        _addUiLog('[MIC] POST /transcribe started');
        try {
          final res = await req.send();
          _addUiLog('[MIC] Upload response status: ${res.statusCode}');
          if (res.statusCode == 200) {
            final body = await res.stream.bytesToString();
            _addUiLog('[MIC] Upload response body: $body');
            final data = json.decode(body);
            if (data['success'] == true && data['text'] != null && (data['text'] as String).trim().isNotEmpty) {
              if (mounted) _textController.clear();
              _sendMessage(data['text'] as String);
            } else {
              if (mounted) _textController.clear();
              await _setAvatarState(AvatarState.idle);
              debugPrint('🎙️ [ASR] Empty transcription returned.');
            }
          } else {
            _addUiLog('[MIC] Upload response error: HTTP ${res.statusCode}');
            throw Exception('Transcription HTTP ${res.statusCode}');
          }
        } catch (e) {
          _addUiLog('[MIC] Upload response error: $e');
          if (mounted) {
            setState(() {
              _messages.add(ChatMessage(text: 'System Error: Transcription failed.', isUser: false));
              _textController.clear();
            });
          }
          await _setAvatarState(AvatarState.idle);
        } finally {
          // Always restart Python wakeword detection after recording/upload finishes
          _resumeWakeWord();
        }
      });

      _webMediaRecorder!.start(200); // Request data every 200ms
      _addUiLog('[FLUTTER] recording started');
      _addUiLog('[MIC] Recording started');
      if (_isHandsFreeMode) {
        _startSilenceDetection();
      }
    } else {
      setState(() {
        _isListening = false;
        _textController.text = 'Transcribing...';
      });
      await _setAvatarState(AvatarState.thinking);
      _stopSilenceDetection();

      // Small pad so trailing syllables are captured
      await Future.delayed(const Duration(milliseconds: 400));
      _webMediaRecorder?.stop();
      _addUiLog('[MIC] Recording stopped');
    }
  }

  void _startSilenceDetection() async {
    try {
      final mediaDevices = html.window.navigator.mediaDevices;
      if (mediaDevices == null) {
        debugPrint('⚠️ VAD: mediaDevices is null');
        return;
      }
      final stream = await mediaDevices.getUserMedia({'audio': true});
      _vadStream = stream;

      final audioCtx = wa.AudioContext();
      _vadAudioContext = audioCtx;
      
      final analyser = audioCtx.createAnalyser();
      _vadAnalyser = analyser;
      analyser.fftSize = 256;

      final source = audioCtx.createMediaStreamSource(stream);
      source.connectNode(analyser);

      final bufferLength = analyser.frequencyBinCount ?? 0;
      final dataArray = Float32List(bufferLength);

      bool hasSpoken = false;
      int silenceTicks = 0;
      int maxTicks = 100; // 10 seconds max timeout (100 * 100ms)
      int elapsedTicks = 0;

      const silenceThreshold = 0.008; 
      const speechThreshold = 0.02;

      _vadTimer = Timer.periodic(const Duration(milliseconds: 100), (timer) async {
        if (!mounted || !_isListening) {
          _stopSilenceDetection();
          return;
        }

        elapsedTicks++;
        if (elapsedTicks >= maxTicks) {
          debugPrint('⏱️ VAD: Max recording duration reached (10s). Auto-stopping...');
          _stopSilenceDetection();
          if (_isListening) {
            await _listen();
          }
          return;
        }

        analyser.getFloatTimeDomainData(dataArray);

        double sum = 0;
        for (int i = 0; i < bufferLength; i++) {
          sum += dataArray[i] * dataArray[i];
        }
        double rms = math.sqrt(sum / bufferLength);

        if (rms > speechThreshold) {
          if (!hasSpoken) {
            hasSpoken = true;
            debugPrint('🗣️ VAD: Speech detected (RMS: ${rms.toStringAsFixed(4)})');
          }
          silenceTicks = 0;
        } else if (rms < silenceThreshold) {
          if (hasSpoken) {
            silenceTicks++;
            if (silenceTicks >= 15) { // 1.5 seconds of silence
              debugPrint('🤫 VAD: Silence detected after speech (1.5s). Auto-stopping...');
              _stopSilenceDetection();
              if (_isListening) {
                await _listen();
              }
            }
          } else {
            if (elapsedTicks >= 40) { // 4 seconds of initial silence
              debugPrint('⏱️ VAD: No speech detected for 4s. Auto-stopping...');
              _stopSilenceDetection();
              if (_isListening) {
                await _listen();
              }
            }
          }
        } else {
          silenceTicks = 0;
        }
      });

    } catch (e) {
      debugPrint('⚠️ VAD Initialization failed: $e');
      _vadTimer = Timer(const Duration(seconds: 5), () async {
        if (mounted && _isListening) {
          debugPrint('⏱️ VAD Fallback: Auto-stopping after 5s...');
          await _listen();
        }
      });
    }
  }

  void _stopSilenceDetection() {
    _vadTimer?.cancel();
    _vadTimer = null;
    
    try {
      final tracks = _vadStream?.getTracks();
      if (tracks != null) {
        for (var track in tracks) {
          track.stop();
        }
      }
    } catch (_) {}
    _vadStream = null;

    try {
      _vadAudioContext?.close();
    } catch (_) {}
    _vadAudioContext = null;
    _vadAnalyser = null;
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Hands-free / wake-word
  // ─────────────────────────────────────────────────────────────────────────

  void _toggleHandsFree() {
    setState(() => _isHandsFreeMode = !_isHandsFreeMode);
  }

  String get _wakeWordUrl {
    final host = html.window.location.hostname ?? '';
    final socketHost = host.isEmpty ? 'localhost' : host;
    final port = html.window.location.port;
    final wsPort = (port != null && port.isNotEmpty) ? ':$port' : '';
    // Use the same protocol scheme - wss for https, ws for http
    final protocol = html.window.location.protocol == 'https:' ? 'wss' : 'ws';
    return '$protocol://$socketHost$wsPort/wakeword';
  }

  /// Tells Python to release its mic (pause_wakeword) or reclaim it (resume_wakeword).
  void _pauseWakeWord() {
    _addUiLog('[FLUTTER] sent pause_wakeword');
    _sendWakeWordCommand({'action': 'pause_wakeword'});
  }

  void _resumeWakeWord() {
    _addUiLog('[FLUTTER] sending resume_wakeword');
    _sendWakeWordCommand({'action': 'resume_wakeword'});
    _addUiLog('[FLUTTER] sent resume_wakeword');
  }

  Future<void> _handleWakeWordEvent() async {
    _addUiLog('[FLUTTER] wake event received');
    
    if (_isTtsInProgress) {
      _addUiLog('[WAKE] blocked because: TTS is in progress');
    } else if (_isListening) {
      _addUiLog('[WAKE] blocked because: already listening');
    } else if (_isInteractionBlocked) {
      _addUiLog('[WAKE] blocked because: interaction is blocked');
    } else {
      _addUiLog('[WAKE] triggering manual mic flow');
      if (mounted) {
        setState(() {
          _isHandsFreeMode = true; // Auto-enable hands-free mode
        });
      }
      _addUiLog('[WAKE] mic flow started');
      await _listen(); // start recording
    }
  }

  // ─── Wake Word WebSocket helpers ─────────────────────────────────────────

  /// Send any action to Python via the WS proxy.
  /// If the socket is closed/closing, reconnect first then send after open.
  void _sendWakeWordCommand(Map<String, dynamic> payload) {
    final msg = json.encode(payload);
    if (_wakeWordSocket != null &&
        _wakeWordSocket!.readyState == html.WebSocket.OPEN) {
      _wakeWordSocket!.send(msg);
    } else {
      _addUiLog('[WAKE WS] not open – reconnecting then sending: ${payload["action"]}');
      _connectWakeWord(onConnected: () {
        if (_wakeWordSocket != null &&
            _wakeWordSocket!.readyState == html.WebSocket.OPEN) {
          _wakeWordSocket!.send(msg);
          _addUiLog('[WAKE WS] sent after reconnect: ${payload["action"]}');
        }
      });
    }
  }

  void _connectWakeWord({VoidCallback? onConnected}) {
    // Don't open a second socket if one is already connecting or open
    if (_wakeWordSocket != null) {
      final rs = _wakeWordSocket!.readyState;
      if (rs == html.WebSocket.OPEN || rs == html.WebSocket.CONNECTING) {
        _addUiLog('[WAKE WS] already open/connecting – skipping duplicate connect');
        if (rs == html.WebSocket.OPEN) onConnected?.call();
        return;
      }
    }

    if (mounted) setState(() => _wakeWsStatus = 'connecting');
    _addUiLog('[WAKE WS] connecting...');
    debugPrint('🔌 [WAKE WS] connecting to ${_wakeWordUrl}');

    try {
      _wakeWordSocket = html.WebSocket(_wakeWordUrl);
    } catch (e) {
      _addUiLog('[WAKE WS] error: $e');
      if (mounted) setState(() => _wakeWsStatus = 'error');
      return;
    }

    _wakeWordSocket!.onOpen.listen((_) {
      if (mounted) setState(() => _wakeWsStatus = 'connected');
      _wakeWsReconnecting = false;
      _addUiLog('[WAKE WS] connected');
      debugPrint('✅ [WAKE WS] connected');
      // Sync persona
      _wakeWordSocket!.send(json.encode({
        'action': 'set_persona',
        'persona': _currentPersona
      }));
      onConnected?.call();
    });

    _wakeWordSocket!.onMessage.listen((event) async {
      debugPrint('📥 Wake-word WS raw message: ${event.data}');
      _addUiLog('[WAKE] message received: ${event.data}');
      try {
        final data = json.decode(event.data.toString());
        if (data['event'] == 'WAKE_WORD_DETECTED') {
          _addUiLog('[WAKE] event parsed: WAKE_WORD_DETECTED');
          await _handleWakeWordEvent();
        }
      } catch (e) {
        debugPrint('Error parsing wake word message: $e');
      }
    });

    _wakeWordSocket!.onError.listen((html.Event errorEvent) {
      if (mounted) setState(() => _wakeWsStatus = 'error');
      _addUiLog('[WAKE WS] error: ${errorEvent.type}');
      debugPrint('❌ [WAKE WS] error: ${errorEvent.type}');
    });

    _wakeWordSocket!.onClose.listen((html.Event event) {
      if (mounted) setState(() => _wakeWsStatus = 'disconnected');
      String reason = '';
      if (event is html.CloseEvent) {
        reason = 'code=${event.code}, reason=${event.reason}, clean=${event.wasClean}';
      }
      _addUiLog('[WAKE WS] disconnected ($reason)');
      debugPrint('⚠️ [WAKE WS] disconnected: $reason');

      // Auto-reconnect – with guard to avoid multiple pending reconnects
      if (mounted && !_wakeWsReconnecting) {
        _wakeWsReconnecting = true;
        _addUiLog('[WAKE WS] reconnecting in 5s...');
        Future.delayed(const Duration(seconds: 5), () {
          if (mounted) {
            _addUiLog('[WAKE WS] reconnecting...');
            _connectWakeWord();
          }
        });
      }
    });
  }

  void _disconnectWakeWord() {
    debugPrint('⚠️ [DEBUG] Calling _wakeWordSocket?.close() from _disconnectWakeWord()');
    _wakeWordSocket?.close();
    _wakeWordSocket = null;
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Send message
  // ─────────────────────────────────────────────────────────────────────────

  Future<void> _sendMessage(String text) async {
    if (text.trim().isEmpty) return;
    _unlockAudio();

    setState(() => _messages.add(ChatMessage(text: text, isUser: true)));
    _textController.clear();
    _scrollToBottom();
    
    // Add a thinking placeholder in chat
    final thinkingIndex = _messages.length;
    setState(() => _messages.add(ChatMessage(text: '__THINKING__', isUser: false)));
    _scrollToBottom();
    
    await _setAvatarState(AvatarState.thinking);

    try {
      final response = await http
          .post(
            Uri.parse('$baseUrl/ask-gpt'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'question': text}),
          )
          .timeout(const Duration(seconds: 30));

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        if (data['success'] == true) {
          final answer = data['answer'] as String;
          final newPersona = data['persona'] as String?;
          if (newPersona != null && newPersona != _currentPersona) {
            setState(() => _currentPersona = newPersona);
            _loadVideo(_avatarState, force: true);
            if (_wakeWordSocket != null && _wakeWordSocket!.readyState == html.WebSocket.OPEN) {
              _wakeWordSocket!.send(json.encode({
                'action': 'set_persona',
                'persona': newPersona
              }));
            }
          }
          // Replace thinking placeholder with actual response
          setState(() {
            if (thinkingIndex < _messages.length && _messages[thinkingIndex].text == '__THINKING__') {
              _messages[thinkingIndex] = ChatMessage(text: '$_pName($_answeringEmoji): $answer', isUser: false);
            } else {
              _messages.add(ChatMessage(text: '$_pName($_answeringEmoji): $answer', isUser: false));
            }
          });
          _scrollToBottom();
          _speak(answer);
        } else {
          throw Exception(data['message'] ?? 'Unknown error');
        }
      } else {
        throw Exception('Server error ${response.statusCode}');
      }
    } catch (e) {
      debugPrint('Send error: $e');
      const errMsg =
          "Oops! I couldn't process that right now. Please try again!";
      // Replace thinking placeholder with error
      setState(() {
        if (thinkingIndex < _messages.length && _messages[thinkingIndex].text == '__THINKING__') {
          _messages[thinkingIndex] = ChatMessage(text: '$_pName(❌): $errMsg', isUser: false);
        } else {
          _messages.add(ChatMessage(text: '$_pName(❌): $errMsg', isUser: false));
        }
      });
      _speak(errMsg);
    } finally {
      _scrollToBottom();
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  PDF upload
  // ─────────────────────────────────────────────────────────────────────────

  Future<void> _uploadPdf() async {
    final result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['pdf'],
      withData: true,
    );
    if (result == null || result.files.single.bytes == null) return;

    final bytes = result.files.single.bytes!;
    final filename = result.files.single.name;
    setState(() => _messages.add(
        ChatMessage(text: 'System: Uploading PDF...', isUser: false)));

    try {
      final req =
          http.MultipartRequest('POST', Uri.parse('$baseUrl/upload-pdf'));
      req.files.add(http.MultipartFile.fromBytes(
        'pdf',
        bytes,
        filename: filename,
        contentType: MediaType('application', 'pdf'),
      ));
      final res = await req.send();
      setState(() => _messages.add(ChatMessage(
          text: res.statusCode == 200
              ? 'System: PDF uploaded successfully!'
              : 'System Error: Upload failed (${res.statusCode}).',
          isUser: false)));
    } catch (e) {
      setState(() => _messages.add(
          ChatMessage(text: 'System Error: PDF upload failed.', isUser: false)));
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Persona switch
  // ─────────────────────────────────────────────────────────────────────────

  Future<void> _switchPersona(String newPersona) async {
    if (_isTtsInProgress) return;
    _unlockAudio();
    setState(() => _currentPersona = newPersona);

    await _loadVideo(AvatarState.talking, force: true); // Force talking animation on switch

    try {
      await http.post(
        Uri.parse('$baseUrl/switch-persona'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'persona': newPersona}),
      );
      if (_wakeWordSocket != null && _wakeWordSocket!.readyState == html.WebSocket.OPEN) {
        _wakeWordSocket!.send(json.encode({
          'action': 'set_persona',
          'persona': newPersona
        }));
      }
    } catch (_) {}

    final greeting = newPersona == 'linda'
        ? "Hi! I'm Linda, your robotic assistant. How can I help you today?"
        : "Hey! I'm John, your robotic assistant. What can I do for you?";
    setState(() => _messages.add(
        ChatMessage(text: '$_pName($_idleEmoji): $greeting', isUser: false)));
    _scrollToBottom();
    _speak(greeting);
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

  void _unlockAudio() {
    try {
      final dummy = html.AudioElement()
        ..src = 'data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA';
      dummy.play().catchError((_) {});
    } catch (_) {}
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Build helpers
  // ─────────────────────────────────────────────────────────────────────────

  Widget _buildAvatar() {
    // Fetch the cached controllers for the active persona's three states
    final idleAsset = _assetFor(_currentPersona, AvatarState.idle);
    final thinkingAsset = _assetFor(_currentPersona, AvatarState.thinking);
    final talkingAsset = _assetFor(_currentPersona, AvatarState.talking);

    final idleController = _cachedControllers[idleAsset];
    final thinkingController = _cachedControllers[thinkingAsset];
    final talkingController = _cachedControllers[talkingAsset];

    // Show indicator if the three videos are not fully loaded yet
    if (idleController == null || !idleController.value.isInitialized ||
        thinkingController == null || !thinkingController.value.isInitialized ||
        talkingController == null || !talkingController.value.isInitialized) {
      return Container(
        color: Colors.white,
        child: const Center(child: CircularProgressIndicator(color: Colors.green)),
      );
    }

    // Helper to compile individual video player viewport
    Widget buildPlayer(VideoPlayerController controller, bool isActive) {
      final double factor = _currentPersona == 'linda' ? 1.0 : 0.8;
      final bool isTalking = controller.dataSource.contains('talking');

      Widget player = FittedBox(
        fit: isTalking ? BoxFit.cover : BoxFit.contain,
        child: SizedBox(
          width: controller.value.size.width,
          height: controller.value.size.height,
          child: VideoPlayer(controller, key: ValueKey(controller)),
        ),
      );

      if (!isTalking) {
        player = FractionallySizedBox(
          widthFactor: factor,
          heightFactor: factor,
          child: player,
        );
      }

      return Container(
        key: ValueKey(controller.dataSource),
        color: Colors.white,
        width: double.infinity,
        height: double.infinity,
        child: IgnorePointer(
          ignoring: !isActive,
          child: player,
        ),
      );
    }

    Widget idlePlayer = buildPlayer(idleController, _avatarState == AvatarState.idle);
    Widget thinkingPlayer = buildPlayer(thinkingController, _avatarState == AvatarState.thinking);
    Widget talkingPlayer = buildPlayer(talkingController, _avatarState == AvatarState.talking);

    List<Widget> stackChildren = [];
    
    // 1. Add inactive players to the bottom of the stack
    if (_avatarState != AvatarState.idle) stackChildren.add(idlePlayer);
    if (_avatarState != AvatarState.thinking) stackChildren.add(thinkingPlayer);
    if (_avatarState != AvatarState.talking) stackChildren.add(talkingPlayer);
    
    // 2. Add active player to the top of the stack (rendered last = on top)
    if (_avatarState == AvatarState.idle) stackChildren.add(idlePlayer);
    if (_avatarState == AvatarState.thinking) stackChildren.add(thinkingPlayer);
    if (_avatarState == AvatarState.talking) stackChildren.add(talkingPlayer);

    return Stack(
      children: stackChildren,
    );
  }

  Widget _buildVisualizer() {
    final bool shouldAnimate = _isTtsInProgress || _avatarState == AvatarState.talking || _isListening;
    
    Widget buildBars(double animationValue) {
      return Row(
        mainAxisAlignment: MainAxisAlignment.center,
        mainAxisSize: MainAxisSize.min,
        children: List.generate(6, (i) {
          final h = 6.0 +
              18.0 *
                  math
                      .sin((animationValue * math.pi * 2) + i * 0.8)
                      .abs();
          return Container(
            margin: const EdgeInsets.symmetric(horizontal: 2),
            width: 4,
            height: h,
            decoration: BoxDecoration(
              color: Colors.greenAccent,
              borderRadius: BorderRadius.circular(4),
            ),
          );
        }),
      );
    }

    if (!shouldAnimate) {
      // If we should not animate, stop the controller ticker and return a static list of bars
      if (_vizController.isAnimating) {
        _vizController.stop();
      }
      return buildBars(0.0); // Stationary visualizer
    }

    // Start the ticker if it was stopped
    if (!_vizController.isAnimating) {
      _vizController.repeat(reverse: true);
    }

    return AnimatedBuilder(
      animation: _vizController,
      builder: (_, __) => buildBars(_vizController.value),
    );
  }

  Widget _buildCollapsedBarContent() {
    final bool isTalking = _isTtsInProgress || _avatarState == AvatarState.talking;
    if (isTalking) {
      return Center(
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          mainAxisSize: MainAxisSize.min,
          children: [
            Flexible(
              child: Text(
                '$_pName($_answeringEmoji): $_pName is talking ',
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 15,
                  fontWeight: FontWeight.bold,
                ),
                overflow: TextOverflow.ellipsis,
              ),
            ),
            _buildBouncingDots(color: Colors.greenAccent),
          ],
        ),
      );
    }
    
    final bool isThinking = _avatarState == AvatarState.thinking ||
        (_messages.isNotEmpty && _messages.last.text == '__THINKING__');
    if (isThinking) {
      return Center(
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          mainAxisSize: MainAxisSize.min,
          children: [
            Flexible(
              child: Text(
                '$_pName($_idleEmoji): $_pName is thinking ',
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 15,
                  fontWeight: FontWeight.bold,
                ),
                overflow: TextOverflow.ellipsis,
              ),
            ),
            _buildBouncingDots(color: Colors.amber),
          ],
        ),
      );
    }
    
    return Row(
      children: [
        _circleBtn(
          _isListening ? Icons.mic : Icons.mic_none,
          _isInteractionBlocked
              ? Colors.grey
              : (_isListening ? Colors.red : Colors.blue),
          _isInteractionBlocked ? () {} : _listen,
        ),
        const SizedBox(width: 10),
        if (_isListening) ...[
          _buildVisualizer(),
          const SizedBox(width: 10),
        ],
        Expanded(
          child: Text(
            'Ask $_pName anything...',
            style: const TextStyle(color: Colors.white, fontSize: 14),
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }

  /// Three bouncing dots animation for "Speaking..." and "Thinking..."
  Widget _buildBouncingDots({Color color = Colors.white}) {
    return AnimatedBuilder(
      animation: _vizController,
      builder: (_, __) {
        return Row(
          mainAxisSize: MainAxisSize.min,
          children: List.generate(3, (i) {
            final offset = 4.0 *
                math.sin((_vizController.value * math.pi * 2) + i * 1.2).abs();
            return Container(
              margin: const EdgeInsets.symmetric(horizontal: 2),
              child: Transform.translate(
                offset: Offset(0, -offset),
                child: Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(
                    color: color,
                    shape: BoxShape.circle,
                  ),
                ),
              ),
            );
          }),
        );
      },
    );
  }

  Widget _buildChatBubble(ChatMessage msg) {
    // Thinking placeholder → show bouncing dots
    if (!msg.isUser && msg.text == '__THINKING__') {
      return Align(
        alignment: Alignment.centerLeft,
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 3, horizontal: 4),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          constraints: const BoxConstraints(maxWidth: 340),
          decoration: BoxDecoration(
            color: Colors.grey[800],
            borderRadius: const BorderRadius.only(
              topLeft: Radius.circular(16),
              topRight: Radius.circular(16),
              bottomLeft: Radius.circular(4),
              bottomRight: Radius.circular(16),
            ),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text('$_pName is thinking ',
                  style: const TextStyle(color: Colors.white70, fontSize: 13, fontStyle: FontStyle.italic)),
              _buildBouncingDots(color: Colors.amber),
            ],
          ),
        ),
      );
    }
    return Align(
      alignment: msg.isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 3, horizontal: 4),
        padding:
            const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        constraints: const BoxConstraints(maxWidth: 340),
        decoration: BoxDecoration(
          color: msg.isUser ? Colors.green[700] : Colors.grey[800],
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(16),
            topRight: const Radius.circular(16),
            bottomLeft: Radius.circular(msg.isUser ? 16 : 4),
            bottomRight: Radius.circular(msg.isUser ? 4 : 16),
          ),
        ),
        child: Text(
          msg.text,
          style: const TextStyle(color: Colors.white, fontSize: 13),
        ),
      ),
    );
  }

  Widget _circleBtn(IconData icon, Color color, VoidCallback onTap,
      {double radius = 22}) {
    return GestureDetector(
      onTap: onTap,
      child: CircleAvatar(
        radius: radius,
        backgroundColor: color,
        child: Icon(icon, color: Colors.white, size: radius * 0.9),
      ),
    );
  }

  Widget _buildExpandedChat() {
    return Container(
      height: 380, // Fixed height!
      padding: const EdgeInsets.fromLTRB(14, 14, 14, 12),
      decoration: BoxDecoration(
        color: Colors.grey[900]!.withOpacity(0.93),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.white12),
      ),
      child: Column(
        children: [
          // ── Chat history ──────────────────────────────────
          Expanded(
            child: _messages.isEmpty
                ? const Center(
                    child: Text('No messages yet',
                        style: TextStyle(color: Colors.white38)))
                : ListView.builder(
                    controller: _scrollController,
                    itemCount: _messages.length,
                    itemBuilder: (_, i) => _buildChatBubble(_messages[i]),
                  ),
          ),
          const SizedBox(height: 8),

          // ── Visualizer when listening ─────────────────────
          if (_isListening) ...[
            _buildVisualizer(),
            const SizedBox(height: 8),
          ],

          // ── Preset sample questions + Stop + Clear Row ───
          Align(
            alignment: Alignment.centerLeft,
            child: Wrap(
              alignment: WrapAlignment.start,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                _buildPresetQuestion("Pick up?", "How do I instruct the robotic arm to pick up a screwdriver?"),
                _buildPresetQuestion("Payload?", "What are the payload limits of this robotic arm?"),
                _buildPresetQuestion("Calibrate?", "Can you explain the calibration process for the arm?"),
                _buildPillBtn("Stop", Icons.stop, Colors.amber[700]!, _stopSpeaking, allowAlways: true),
                _buildPillBtn("Clear", Icons.delete, Colors.red[600]!, _clearChat, allowAlways: true),
              ],
            ),
          ),
          const SizedBox(height: 8),

          // ── Input row ─────────────────────────────────────
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _textController,
                  style: const TextStyle(color: Colors.white),
                  decoration: InputDecoration(
                     hintText: 'Type a question...',
                    hintStyle: const TextStyle(color: Colors.green),
                    filled: true,
                    fillColor: Colors.grey[800],
                    contentPadding: const EdgeInsets.symmetric(
                        horizontal: 16, vertical: 10),
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(25),
                      borderSide: BorderSide.none,
                    ),
                  ),
                  onSubmitted: _sendMessage,
                ),
              ),
              const SizedBox(width: 8),
              // Headphone (hands-free toggle) - green when active (LEFT of mic)
              _circleBtn(
                Icons.headphones,
                _isHandsFreeMode ? Colors.green : Colors.grey[600]!,
                _toggleHandsFree,
                radius: 18,
              ),
              const SizedBox(width: 6),
              // Mic
              _circleBtn(
                _isListening ? Icons.mic : Icons.mic_none,
                _isInteractionBlocked
                    ? Colors.grey
                    : (_isListening ? Colors.red : Colors.blue),
                _isInteractionBlocked ? () {} : _listen,
                radius: 22,
              ),
              const SizedBox(width: 6),
              // Send
              _circleBtn(
                Icons.send,
                _isInteractionBlocked ? Colors.grey : Colors.green,
                _isInteractionBlocked ? () {} : () => _sendMessage(_textController.text),
                radius: 22,
              ),
            ],
          ),
        ],
      ),
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Build
  // ─────────────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      body: Stack(
        children: [
          // ── Avatar (full screen) ──────────────────────────
          Positioned.fill(child: _buildAvatar()),

          // ── Debug Overlay ─────────────────────────────────
          if (_micDebugLogs.isNotEmpty)
            Positioned(
              top: 80,
              left: 20,
              child: Container(
                width: 350,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: Colors.black.withOpacity(0.8),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: Colors.green),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text('🎙️ MIC DEBUG LOGS', style: TextStyle(color: Colors.green, fontWeight: FontWeight.bold, fontSize: 12)),
                            Text('WS: $_wakeWsStatus', style: TextStyle(
                              color: _wakeWsStatus == 'connected' ? Colors.greenAccent :
                                     _wakeWsStatus == 'connecting' ? Colors.orange :
                                     Colors.redAccent,
                              fontSize: 10,
                            )),
                          ],
                        ),
                        InkWell(
                          onTap: () => setState(() => _micDebugLogs.clear()),
                          child: const Icon(Icons.close, color: Colors.white54, size: 16),
                        )
                      ],
                    ),
                    const SizedBox(height: 8),
                    ElevatedButton(
                      onPressed: () => _handleWakeWordEvent(),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: Colors.blueAccent,
                        minimumSize: const Size.fromHeight(36),
                      ),
                      child: const Text('Simulate Wake Word', style: TextStyle(color: Colors.white)),
                    ),
                    const SizedBox(height: 8),
                    ..._micDebugLogs.map((log) => Text(
                          log,
                          style: const TextStyle(color: Colors.white, fontSize: 11, fontFamily: 'monospace'),
                        )),
                  ],
                ),
              ),
            ),

          // ── Logo ──────────────────────────────────────────
          Positioned(
            top: 20,
            right: 20,
            child: SafeArea(
                child: Image.asset('assets/singaporepoly.png', height: 40)),
          ),

          // ── E-STOP Button ─────────────────────────────────
          Positioned(
            top: 20,
            right: 170,
            child: SafeArea(
              child: ElevatedButton.icon(
                onPressed: _emergencyStop,
                icon: const Icon(Icons.warning, size: 18, color: Colors.white),
                label: const Text('E-STOP',
                    style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13)),
                style: ElevatedButton.styleFrom(
                  backgroundColor: Colors.red[800],
                  foregroundColor: Colors.white,
                  elevation: 8,
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(20),
                    side: const BorderSide(color: Colors.white, width: 2),
                  ),
                ),
              ),
            ),
          ),

          // ── Back button ───────────────────────────────────
          Positioned(
            top: 20,
            left: 20,
            child: SafeArea(
              child: ElevatedButton.icon(
                onPressed: () => Navigator.pop(context),
                icon: const Icon(Icons.arrow_back, size: 16),
                label: const Text('Back'),
                style: ElevatedButton.styleFrom(
                  backgroundColor: Colors.grey[600],
                  foregroundColor: Colors.white,
                  padding: const EdgeInsets.symmetric(
                      horizontal: 16, vertical: 8),
                  shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(20)),
                ),
              ),
            ),
          ),

          // ── Collapsed status bar (menu hidden) ────────────
          if (!_isMenuVisible)
            Positioned(
              bottom: 76,
              left: 80,
              right: 80,
              height: 68, // Fixed height for consistency and shape!
              child: Container(
                padding: const EdgeInsets.symmetric(
                    horizontal: 18, vertical: 12),
                decoration: BoxDecoration(
                  color: Colors.grey[900]!.withOpacity(0.87),
                  borderRadius: BorderRadius.circular(14),
                  border: Border.all(color: Colors.white24),
                ),
                child: _buildCollapsedBarContent(),
              ),
            ),

          // ── Expanded chat panel ───────────────────────────
          if (_isMenuVisible)
            Positioned(
              bottom: 88,
              left: 14,
              right: 14,
              child: _buildExpandedChat(),
            ),

          // ── Persona switch – bottom left ──────────────────
          Positioned(
            bottom: 26,
            left: 16,
            child: GestureDetector(
              onTap: () => _switchPersona(
                  _currentPersona == 'john' ? 'linda' : 'john'),
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 300),
                padding: const EdgeInsets.symmetric(
                    horizontal: 16, vertical: 10),
                decoration: BoxDecoration(
                  color: _currentPersona == 'linda'
                      ? Colors.pink[600]
                      : Colors.blue[700],
                  borderRadius: BorderRadius.circular(30),
                  boxShadow: [
                    BoxShadow(
                      color: (_currentPersona == 'linda'
                              ? Colors.pink
                              : Colors.blue)
                          .withOpacity(0.45),
                      blurRadius: 10,
                      spreadRadius: 2,
                    ),
                  ],
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      _currentPersona == 'linda'
                          ? Icons.female
                          : Icons.male,
                      color: Colors.white,
                      size: 20,
                      key: const ValueKey('persona_icon'),
                    ),
                    const SizedBox(width: 6),
                    Text(
                      _currentPersona == 'linda' ? 'Linda ⇆' : 'John ⇆',
                      style: const TextStyle(
                          color: Colors.white,
                          fontWeight: FontWeight.bold,
                          fontSize: 15),
                    ),
                  ],
                ),
              ),
            ),
          ),

          // ── Upload PDF + CC Toggle + Menu toggle – bottom right ──
          Positioned(
            bottom: 26,
            right: 16,
            child: Row(
              children: [
                if (_isMenuVisible) ...[
                  ElevatedButton.icon(
                    onPressed: _uploadPdf,
                    icon: const Icon(Icons.upload_file, size: 16),
                    label: const Text('Upload PDF'),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: Colors.grey[700],
                      foregroundColor: Colors.white,
                      shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(20)),
                      padding: const EdgeInsets.symmetric(
                          horizontal: 16, vertical: 10),
                    ),
                  ),
                  const SizedBox(width: 8),
                ],
                ElevatedButton.icon(
                  onPressed: () => setState(() => _isCcEnabled = !_isCcEnabled),
                  icon: Icon(
                    _isCcEnabled ? Icons.closed_caption : Icons.closed_caption_disabled,
                    size: 16,
                  ),
                  label: Text(_isCcEnabled ? 'CC On' : 'CC Off'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.blue[600],
                    foregroundColor: Colors.white,
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(20)),
                    padding: const EdgeInsets.symmetric(
                        horizontal: 16, vertical: 10),
                  ),
                ),
                const SizedBox(width: 8),
                ElevatedButton.icon(
                  onPressed: () =>
                      setState(() => _isMenuVisible = !_isMenuVisible),
                  icon: const Icon(Icons.menu, size: 16),
                  label: Text(_isMenuVisible ? 'Hide Menu' : 'Menu'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.grey[700],
                    foregroundColor: Colors.white,
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(20)),
                    padding: const EdgeInsets.symmetric(
                        horizontal: 16, vertical: 10),
                  ),
                ),
              ],
            ),
          ),

          // ── Subtitle Overlay ──
          // Separate box above the message box showing what the robot is saying
          Positioned(
            bottom: _isMenuVisible ? 518 : 194, // Exactly 50px above the message box!
            left: 30,
            right: 30,
            height: 110, // Strictly bounded height to prevent layout crashes on Flutter Web!
            child: Visibility(
              visible: _isCcEnabled && _isTtsInProgress && (_currentSubtitleText ?? '').isNotEmpty,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
                decoration: BoxDecoration(
                  color: Colors.black.withOpacity(0.85),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: Colors.white24),
                ),
                child: Scrollbar(
                  controller: _subtitleScrollController,
                  thumbVisibility: true,
                  child: SingleChildScrollView(
                    controller: _subtitleScrollController,
                    physics: const BouncingScrollPhysics(),
                    child: Container(
                      alignment: Alignment.center,
                      width: double.infinity,
                      child: Text(
                        _currentSubtitleText ?? '',
                        textAlign: TextAlign.center,
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 15,
                          fontWeight: FontWeight.w500,
                          height: 1.4,
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
