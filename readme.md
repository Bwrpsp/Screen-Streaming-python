# Real-Time H.264 TCP SSL/TLS Screen & Audio Streaming

A high-performance, real-time Python application that streams the host's screen (compressed using **H.264**) and audio to connected clients over an encrypted TCP connection (SSL/TLS).

## Features

- **Encrypted Transmission**: Wraps the network socket inside a TLS/SSL tunnel to guarantee secure data transmission.
- **Dynamic Certificate Generation**: Dynamically creates self-signed SSL/TLS certificates (`cert.pem`, `key.pem`) on startup using the Python `cryptography` library.
- **H.264 Video Compression**: Uses `av` (PyAV/FFmpeg) for high-efficiency video encoding. It scales and encodes frames up to 2560x1440 (1440p) at ~25 FPS with an adjustable bitrate (default: 15 Mbps).
- **Low Latency**: Fast encoding presets (`ultrafast`, `zerolatency`) keep video delays minimal.
- **Audio Streaming**: Captures sound from the default input device and streams it in real-time.
- **Multi-Client Support**: Allows multiple clients to connect to the host securely.

---

## Installation & Prerequisites

Both the host and client machines must have Python installed along with the dependencies.

### 1. Install System Dependencies

#### Windows
`pyaudio`, `av`, and `cryptography` wheels are generally available on Windows, so standard installation works out-of-the-box.

#### Linux (Ubuntu/Debian)
If running client/host on Linux, you will need to install development libraries first:
```bash
sudo apt-get install portaudio19-dev python3-pyaudio libasound2-dev libavformat-dev libavcodec-dev libavdevice-dev libavfilter-dev libswscale-dev libswresample-dev libpostproc-dev
```

### 2. Install Python Packages

Navigate to the project directory and run:
```bash
pip install -r requirements.txt
```

---

## How to Run

### 1. Start the Host (`host.py`)
Run `host.py` on the computer you want to stream.

```bash
python host.py
```

*Note:*
- The host will dynamically generate `cert.pem` and `key.pem` in the workspace directory on first startup.
- The host will print its local network IP address (e.g., `192.168.1.100`).
- The host listens on port **9999** for secure incoming TLS connections.

### 2. Start the Client (`client.py`)
Run `client.py` on the receiver computer.

```bash
python client.py
```

- When prompted, enter the **Host IP Address** that was printed by the host.
- A display window will open showcasing the host's screen securely.
- Press **`q`** inside the display window to exit cleanly.

---

## Protocol Details

Every packet sent over the secure connection is framed with a 5-byte header:
```
+---------------------+-------------------------------+----------------------+
| Packet Type (1 byte)| Payload Length (4 bytes, uint)| Payload (N bytes)    |
+---------------------+-------------------------------+----------------------+
```
- **`V` (Video)**: Raw H.264 encoded bitstream frame (encapsulated into a PyAV Packet).
- **`A` (Audio)**: Raw PCM audio bytes.