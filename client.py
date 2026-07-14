import socket
import threading
import time
import struct
import queue
import cv2
import numpy as np
import pyaudio
import av
import ssl

# Configurations
PORT = 9999
AUDIO_RATE = 22050  # Audio sample rate (Hz)
AUDIO_CHANNELS = 1  # 1 = Mono
AUDIO_CHUNK = 1024  # Samples per buffer

# Threading & Queue states
running = True
latest_frame = None
frame_lock = threading.Lock()
audio_queue = queue.Queue(maxsize=50)

def recv_all(sock, length):
    """Utility to receive exactly the specified number of bytes from the TCP socket."""
    data = b''
    while len(data) < length:
        try:
            packet = sock.recv(length - len(data))
            if not packet:
                return None
            data += packet
        except socket.timeout:
            if not running:
                return None
            continue
        except Exception:
            return None
    return data

def recv_msg(sock):
    """Parses a custom framed message from the TCP socket."""
    # Read header (1 byte type + 4 bytes payload length)
    header = recv_all(sock, 5)
    if not header:
        return None, None
    msg_type, length = struct.unpack('!B I', header)
    payload = recv_all(sock, length)
    return chr(msg_type), payload

def receive_packets(sock, decoder):
    """Receives TCP packets and handles H.264 video decoding and audio buffer queueing."""
    global running, latest_frame
    audio_packets_recv = 0
    video_frames_recv = 0

    while running:
        msg_type, payload = recv_msg(sock)
        if not msg_type:
            print("[Client] Connection lost from host.")
            break

        if msg_type == 'V':  # Video packet (H.264 bitstream)
            try:
                packet = av.Packet(payload)
                frames = decoder.decode(packet)
                for frame in frames:
                    # Convert decoded YUV frame back to BGR numpy array
                    bgr_arr = frame.to_ndarray(format='bgr24')
                    with frame_lock:
                        latest_frame = bgr_arr
                    
                    video_frames_recv += 1
                    if video_frames_recv % 25 == 0:
                        print(f"[DEBUG] Decoded video frame {video_frames_recv} (Size: {len(payload)} bytes).")
            except Exception:
                # Discard early decode errors before first GOP keyframe arrives
                pass

        elif msg_type == 'A':  # Audio packet (PCM)
            try:
                audio_queue.put_nowait(payload)
            except queue.Full:
                # Drop audio packets if client buffer overflows to stay real-time
                pass
            
            audio_packets_recv += 1
            if audio_packets_recv % 100 == 0:
                print(f"[DEBUG] Received {audio_packets_recv} audio packets. Queue size: {audio_queue.qsize()}")

    running = False

def audio_play_loop():
    """Retrieves PCM audio data from queue and plays it back in real-time."""
    global running
    p = pyaudio.PyAudio()
    stream = None
    try:
        stream = p.open(
            format=pyaudio.paInt16,
            channels=AUDIO_CHANNELS,
            rate=AUDIO_RATE,
            output=True,
            frames_per_buffer=AUDIO_CHUNK
        )
        print("[Client] Audio playback device successfully initialized.")
    except Exception as e:
        print(f"[Client] WARNING: Failed to initialize audio playback ({e}). Running in video-only mode.")
        p.terminate()
        return

    while running:
        try:
            # Drain queue if we're falling behind to maintain real-time low latency
            q_size = audio_queue.qsize()
            if q_size > 8:
                for _ in range(q_size - 2):
                    try:
                        audio_queue.get_nowait()
                    except queue.Empty:
                        break

            try:
                audio_data = audio_queue.get(timeout=0.1)
                stream.write(audio_data)
            except queue.Empty:
                continue
        except Exception as e:
            if running:
                print(f"[Client] Audio playback error: {e}")
            break

    if stream:
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
    p.terminate()
    print("[Client] Audio playback stopped.")

def main():
    global running, latest_frame

    print("=" * 60)
    print("  Antigravity H.264 Streamer - CLIENT (TCP SSL/TLS RECEIVER)")
    print("=" * 60)

    # Prompt user for host IP
    host_ip = input("Enter the Host IP Address: ").strip()
    if not host_ip:
        print("[Client] Error: Host IP cannot be empty.")
        return

    # Setup standard TCP Socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)

    # Create SSL Context
    # Using PROTOCOL_TLS_CLIENT creates context for client side connections
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # Bypass certificate authority verification for local self-signed certificates
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    # Wrap the socket in SSL/TLS context
    try:
        secure_sock = ssl_context.wrap_socket(sock, server_hostname=host_ip)
    except Exception as e:
        print(f"[Client] ERROR: Failed to wrapping socket in SSL/TLS context: {e}")
        sock.close()
        return

    print(f"[Client] Connecting securely to host at {host_ip}:{PORT}...")
    try:
        secure_sock.connect((host_ip, PORT))
    except Exception as e:
        print(f"[Client] ERROR: Secure connection failed: {e}")
        secure_sock.close()
        return

    # Setup PyAV H.264 Decoder
    codec = av.Codec('h264', 'r')
    decoder = av.CodecContext.create(codec)
    decoder.open()

    print("[Client] Secure connection established. Initializing H.264 stream...")

    # Start secure network and audio play threads
    recv_thread = threading.Thread(target=receive_packets, args=(secure_sock, decoder), daemon=True)
    audio_thread = threading.Thread(target=audio_play_loop, daemon=True)

    recv_thread.start()
    audio_thread.start()

    # Create UI Window
    window_name = f"Secure H.264 Stream - Connected to {host_ip}"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    
    print(f"[Client] Stream started. Open the OpenCV window:")
    print(f"         - Press 'f' to toggle Fullscreen/Windowed")
    print(f"         - Press 'q' to quit")

    try:
        while running:
            frame = None
            with frame_lock:
                if latest_frame is not None:
                    frame = latest_frame
                    latest_frame = None  # Consume frame

            if frame is not None:
                # Render the decoded frame directly
                cv2.imshow(window_name, frame)

            # Wait key handles OpenCV GUI events (crucial!)
            key = cv2.waitKey(10) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('f') or key == ord('F'):
                # Toggle fullscreen
                is_fullscreen = cv2.getWindowProperty(window_name, cv2.WND_PROP_FULLSCREEN)
                if is_fullscreen == cv2.WINDOW_FULLSCREEN:
                    cv2.setWindowProperty(window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_NORMAL)
                else:
                    cv2.setWindowProperty(window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

            # Handle case where user closes OpenCV window via 'X' button
            try:
                if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                    break
            except Exception:
                break

    except KeyboardInterrupt:
        print("\n[Client] Exiting...")
    finally:
        running = False
        secure_sock.close()
        cv2.destroyAllWindows()
        print("[Client] Cleanup complete. Goodbye!")

if __name__ == '__main__':
    main()
