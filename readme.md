# Real-Time H.264 TCP Screen & Audio Streaming

A high-performance, real-time Python application that streams the host's primary monitor screen (compressed using **H.264**) and audio to connected clients over a TCP network.

## Features

- **H.264 Video Compression**: Uses `av` (PyAV/FFmpeg) for high-efficiency video encoding. It scales and encodes frames to 1280x720 (720p) at ~25 FPS with minimal bandwidth footprint.
- **Low Latency & Zero Stutter**: Employs real-time zerolatency tuning. Client-side queues immediately discard outdated frames to maintain live sync.
- **Audio Streaming**: Captures sound from the default input device and streams it in real-time.
- **TCP Socket Reliability**: Guarantees lossless, ordered delivery of frames and audio blocks.
- **Multi-Client Support**: Allows multiple clients to connect to the host simultaneously.

---

## Installation & Prerequisites

Both the host and client machines must have Python installed along with the dependencies.

### 1. Install System Dependencies

#### Windows
`pyaudio` and `av` wheels are generally available on Windows, so standard installation works out-of-the-box.

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
- The host will print its local network IP address (e.g., `192.168.1.100`) on startup. Take note of this IP!
- The host listens on TCP port **9999**.
- Ensure that you allow incoming connections on port `9999` in your Windows Firewall or any local security software.

### 2. Start the Client (`client.py`)
Run `client.py` on the receiver computer.

```bash
python client.py
```

- When prompted, enter the **Host IP Address** that was printed by the host.
- A display window will open showcasing the host's screen.
- Press **`q`** inside the display window to exit cleanly.

---

## Protocol Details

Every packet sent over the TCP connection is framed with a 5-byte header to distinguish message types and sizes:
```
+---------------------+-------------------------------+----------------------+
| Packet Type (1 byte)| Payload Length (4 bytes, uint)| Payload (N bytes)    |
+---------------------+-------------------------------+----------------------+
```
- **`V` (Video)**: Raw H.264 encoded bitstream frame (encapsulated into a PyAV Packet).
- **`A` (Audio)**: Raw PCM audio bytes.