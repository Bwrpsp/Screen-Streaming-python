import socket
import threading
import time
import struct
import mss
import cv2
import numpy as np
import pyaudio

# Configurations
PORT = 9999
CHUNK_SIZE = 60000  # Max size of UDP packet payload (under 65,507 bytes limit)
FRAME_RATE = 25     # Target frames per second
AUDIO_RATE = 22050  # Audio sample rate (Hz)
AUDIO_CHANNELS = 1  # 1 = Mono, 2 = Stereo
AUDIO_CHUNK = 1024  # Samples per buffer

# Broadcast destination configuration
BROADCAST_ADDR = ('255.255.255.255', PORT)

# Threading states
running = True
screenshot_warning_printed = False

def get_local_ip():
    """Attempts to find the local IP address by creating a dummy UDP connection."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Does not send actual data, just opens a socket to check default interface
        s.connect(('8.8.8.8', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def get_fallback_frame(width=1280, height=720):
    """Generates a dynamic mock screen frame to display if screenshot capture fails."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    
    # Subtle blue-to-black gradient
    for y in range(height):
        val = int(50 * (1 - y / height))
        frame[y, :, 0] = val       # B
        frame[y, :, 1] = int(val * 0.4)  # G
        frame[y, :, 2] = int(val * 0.2)  # R
        
    # Title & Text warnings
    cv2.putText(frame, "Host Screen Stream (Broadcast Mode)", (50, 100),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(frame, "Screen capture failed (session is locked or headless).", (50, 150),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2, cv2.LINE_AA)
    cv2.putText(frame, "Audio streaming continues active.", (50, 200),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2, cv2.LINE_AA)
                
    # Draw a bouncing/rotating circle to visually demonstrate active FPS
    t = time.time()
    cx = int(width / 2 + 150 * np.cos(t * 3.0))
    cy = int(height / 2 + 100 * np.sin(t * 3.0))
    cv2.circle(frame, (cx, cy), 35, (0, 230, 0), -1)
    cv2.circle(frame, (cx, cy), 35, (255, 255, 255), 3)

    # Timestamp representation
    time_str = time.strftime("%Y-%m-%d %H:%M:%S") + f".{int((t % 1) * 100):02d}"
    cv2.putText(frame, f"Time: {time_str}", (50, 260),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (150, 150, 150), 1, cv2.LINE_AA)
                
    return frame

def audio_stream_loop(sock):
    """Captures microphone/input audio and broadcasts it to the network."""
    global running
    p = pyaudio.PyAudio()
    stream = None
    try:
        stream = p.open(
            format=pyaudio.paInt16,
            channels=AUDIO_CHANNELS,
            rate=AUDIO_RATE,
            input=True,
            frames_per_buffer=AUDIO_CHUNK
        )
        print(f"[Host] Audio capture initialized (Rate: {AUDIO_RATE}Hz, Mono).")
    except Exception as e:
        print(f"[Host] WARNING: Audio initialization failed ({e}). Running in video-only mode.")
        p.terminate()
        return

    audio_pkt_count = 0
    while running:
        try:
            # Read audio data from default recording device
            # exception_on_overflow=False prevents crashes if CPU lags
            audio_data = stream.read(AUDIO_CHUNK, exception_on_overflow=False)
            
            # Format audio packet: Header 'A' (1 byte) + raw PCM data
            packet = b'A' + audio_data
            
            # Broadcast to local network
            sock.sendto(packet, BROADCAST_ADDR)
            
            audio_pkt_count += 1
            if audio_pkt_count % 100 == 0:
                print(f"[DEBUG] Broadcasted {audio_pkt_count} audio packets.")
        except Exception as e:
            if running:
                print(f"[Host] Audio streaming error: {e}")
            break

    if stream:
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
    p.terminate()
    print("[Host] Audio streaming stopped.")

def main():
    global running, screenshot_warning_printed
    local_ip = get_local_ip()
    print("=" * 60)
    print(f"  Antigravity UDP Streamer - HOST (BROADCAST MODE)")
    print(f"  Local IP: {local_ip}")
    print(f"  Broadcast Port: {PORT}")
    print("=" * 60)

    # Setup UDP Socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Enable Broadcast option
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    
    # Bind host socket to ephemeral port to avoid conflicts with clients running on the same host machine
    try:
        sock.bind(('0.0.0.0', 0))
    except Exception as e:
        print(f"[Host] ERROR: Could not bind host socket: {e}")
        return

    # Start audio broadcast thread
    audio_thread = threading.Thread(target=audio_stream_loop, args=(sock,), daemon=True)
    audio_thread.start()

    print(f"[Host] Broadcasting screen and audio to {BROADCAST_ADDR[0]}:{BROADCAST_ADDR[1]}...")

    # Screen capture stream
    with mss.mss() as sct:
        # Use primary monitor if available, otherwise virtual/all monitors
        if len(sct.monitors) > 1:
            monitor = sct.monitors[1]
        else:
            monitor = sct.monitors[0]

        frame_id = 0
        frame_interval = 1.0 / FRAME_RATE

        try:
            while running:
                start_time = time.time()

                try:
                    # Capture screen frame
                    sct_img = sct.grab(monitor)
                    frame = np.array(sct_img)
                    # Convert BGRA to BGR for OpenCV
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

                    # Downscale screen for efficient network transmission
                    h, w = frame.shape[:2]
                    target_width = 1280
                    if w > target_width:
                        scale = target_width / w
                        target_height = int(h * scale)
                        frame = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)
                except Exception as e:
                    if not screenshot_warning_printed:
                        print(f"[Host] WARNING: Screen capture failed ({e}). Using fallback test pattern.")
                        screenshot_warning_printed = True
                    frame = get_fallback_frame()

                # Compress frame to JPEG
                success, encoded_img = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
                if not success:
                    continue

                jpeg_bytes = encoded_img.tobytes()
                total_bytes = len(jpeg_bytes)

                # Segment and transmit the video frame
                total_chunks = (total_bytes + CHUNK_SIZE - 1) // CHUNK_SIZE
                for chunk_idx in range(total_chunks):
                    start = chunk_idx * CHUNK_SIZE
                    end = min(start + CHUNK_SIZE, total_bytes)
                    chunk_data = jpeg_bytes[start:end]

                    # Header:
                    # - 'V' (1 byte)
                    # - frame_id (4 bytes, unsigned int)
                    # - chunk_idx (2 bytes, unsigned short)
                    # - total_chunks (2 bytes, unsigned short)
                    header = struct.pack('!B I H H', ord('V'), frame_id, chunk_idx, total_chunks)
                    packet = header + chunk_data

                    try:
                        sock.sendto(packet, BROADCAST_ADDR)
                    except Exception:
                        pass

                if frame_id % 25 == 0:
                    print(f"[DEBUG] Broadcasted frame {frame_id} ({total_bytes} bytes, {total_chunks} chunks).")

                frame_id = (frame_id + 1) % 4294967295  # prevent overflow

                # Calculate frame time and delay to hit target frame rate
                elapsed = time.time() - start_time
                delay = max(0.001, frame_interval - elapsed)
                time.sleep(delay)

        except KeyboardInterrupt:
            print("\n[Host] Shutting down...")
        finally:
            running = False
            sock.close()

if __name__ == '__main__':
    main()
