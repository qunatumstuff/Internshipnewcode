import 'dart:async';
import 'dart:convert';
import 'dart:html' as html; // Used for Audio playback on Web
import 'dart:math' as math;
import 'dart:ui'; // For ImageFilter.blur

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';
import 'package:record/record.dart';
import 'package:video_player/video_player.dart';

class ChatMessage {
  final String text;
  final bool isUser;
  final bool isTyping;
  ChatMessage({required this.text, required this.isUser, this.isTyping = false});
}

class _TypingIndicator extends StatefulWidget {
  final String prefix;
  final double fontSize;
  const _TypingIndicator({this.prefix = "", this.fontSize = 15.0});

  @override
  State<_TypingIndicator> createState() => _TypingIndicatorState();
}

class _TypingIndicatorState extends State<_TypingIndicator> with SingleTickerProviderStateMixin {
  late AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(vsync: this, duration: const Duration(milliseconds: 1200))..repeat();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
 
                const SizedBox(width: 10),
                Text(
                  "Executing: $_currentToolStatus...",
                  style: const TextStyle(
                    color: Colors.white,
                    fontFamily: 'monospace',
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildEStopButton() {
    return Positioned(
      top: 80,
      right: 20,
      child: SafeArea(
        child: ElevatedButton.icon(
          onPressed: _triggerEStop,
          icon: const Icon(Icons.warning_rounded, color: Colors.white),
          label: const Text(
            "E-STOP",
            style: TextStyle(fontWeight: FontWeight.bold, letterSpacing: 1.2),
          ),
          style: ElevatedButton.styleFrom(
            backgroundColor: Colors.redAccent,
            foregroundColor: Colors.white,
            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 15),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(30),
              side: const BorderSide(color: Colors.red, width: 2),
            ),
            elevation: 10,
            shadowColor: Colors.red,
          ),
        ),
      ),
    );
  }

  Widget _buildTopLogo() {
    return Positioned(
      top: 20,
      right: 20,
      child: SafeArea(
        child: Image.asset('assets/inbgsplogo.png', height: 40),
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
  
  Widget _buildPresetQuestion(String label, String question) {
    return Padding(
      padding: const EdgeInsets.only(right: 6),
      child: ActionChip(
        label: Text(
          label,
          style: GoogleFonts.inter(
            color: Colors.black87,
            fontSize: 12,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.3,
          ),
        ),
        backgroundColor: Colors.white.withValues(alpha: 0.9),
        side: const BorderSide(color: Colors.greenAccent, width: 1),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 
                  const SizedBox(width: 8),
                ],
                ElevatedButton.icon(
                  onPressed: () {
                    setState(() => _showSubtitles = !_showSubtitles);
                  },
                  icon: Icon(_showSubtitles ? Icons.subtitles : Icons.subtitles_off, size: 18),
                  label: Text(_showSubtitles ? "CC On" : "CC Off"),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: _showSubtitles ? Colors.blue[700] : Colors.grey[700],
                    foregroundColor: Colors.white,
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                  ),
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
