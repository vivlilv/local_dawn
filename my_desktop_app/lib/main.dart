import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import 'dart:io';
import 'dart:async';

void main() {
  runApp(MyApp());
}

class MyApp extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Telegram Bot Interface',
      theme: ThemeData(
        primarySwatch: Colors.blue,
      ),
      home: MyHomePage(),
    );
  }
}

class MyHomePage extends StatefulWidget {
  @override
  _MyHomePageState createState() => _MyHomePageState();
}

class _MyHomePageState extends State<MyHomePage> {
  final List<Message> _messages = [];

  @override
  void initState() {
    super.initState();
    _showWelcomeMessage();
  }

  void _showWelcomeMessage() {
    setState(() {
      _messages.add(Message(text: "👋 Welcome to the Telegram Bot Interface!\n\n"
          "Please choose an action below:", isUser: false));
    });
  }

  Future<void> _uploadFile() async {
    final result = await FilePicker.platform.pickFiles();
    if (result != null) {
      String filePath = result.files.single.path!;
      String response = await _processExcelFile(filePath);
      setState(() {
        _messages.add(Message(text: response, isUser: false));
      });
    } else {
      setState(() {
        _messages.add(Message(text: 'No file selected.', isUser: false));
      });
    }
  }

  Future<String> _processExcelFile(String filePath) async {
    // Call the Python script and pass the file path
    ProcessResult result = await Process.run('python', ['../callbacks/add_accounts.py', filePath]);

    // Check for errors
    if (result.exitCode != 0) {
      // If there was an error, return the error message
      return 'Error: ${result.stderr}';
    }

    // Return the output from the Python script
    return result.stdout; // Capture the output from the Python script
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('Telegram Bot Interface'),
      ),
      body: Column(
        children: [
          Expanded(
            child: ListView.builder(
              itemCount: _messages.length,
              itemBuilder: (context, index) {
                return _messages[index].isUser
                    ? UserMessage(message: _messages[index].text)
                    : BotMessage(message: _messages[index].text);
              },
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(8.0),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceEvenly,
              children: [
                ElevatedButton(
                  onPressed: _uploadFile,
                  child: Text('Добавить аккаунты'), // Add Accounts
                ),
                ElevatedButton(
                  onPressed: _showStats, // New button for My Stats
                  child: Text('Моя статистика'), // My Stats
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  // New method to handle showing stats
  void _showStats() async {
    // Call the function to get stats from the backend
    String response = await _getStats(); // Implement this function to fetch stats
    setState(() {
      _messages.add(Message(text: response, isUser: false));
    });
  }

  // Function to fetch stats from the backend
  Future<String> _getStats() async {
    ProcessResult result = await Process.run('python', ['..callbacks/get_stats.py'],);

    // Check for errors
    if (result.exitCode != 0) {
      // If there was an error, return the error message
      return 'Error: ${result.stderr}';
    }

    // Return the output from the Python script
    return result.stdout; // Capture the output from the Python script
  }
}

class Message {
  final String text;
  final bool isUser;

  Message({required this.text, required this.isUser});
}

class UserMessage extends StatelessWidget {
  final String message;

  const UserMessage({Key? key, required this.message}) : super(key: key);

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerRight,
      child: Container(
        margin: EdgeInsets.symmetric(vertical: 5, horizontal: 10),
        padding: EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: Colors.blue[100],
          borderRadius: BorderRadius.circular(10),
        ),
        child: Text(message),
      ),
    );
  }
}

class BotMessage extends StatelessWidget {
  final String message;

  const BotMessage({Key? key, required this.message}) : super(key: key);

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: EdgeInsets.symmetric(vertical: 5, horizontal: 10),
        padding: EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: Colors.grey[300],
          borderRadius: BorderRadius.circular(10),
        ),
        child: Text(message),
      ),
    );
  }
}
