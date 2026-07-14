import socket
import threading
import time
import struct
import queue
import cv2
import numpy as np
import pyaudio

# Configurations
PORT = 67
AUDIO_RATE = 22050  # Audio sample rate (Hz)
AUDIO_CHANNELS = 1  # 1 = Mono
AUDIO_CHUNK = 1024  # Samples per buffer

# Threading & Queue states
running = True
latest_frame = None
frame_lock = threading.Lock()
audio_queue = queue.Queue(maxsize=50)

def ping_host(sock, host_ip):
    """Sends periodic ping packets to host to register and keep the connection alive."""
    global running
    print(f"[Client] Connection thread started. Pinging {host_ip}:{PORT}...")
    while running:
        try:
            sock.sendto(b'P', (host_ip, PORT))
        except Exception as e:
            print(f"[Client] Ping error: {e}")
        time.sleep(2.0)

def receive_packets(sock):
    """Receives UDP packets, parses them, handles audio play queues, and reassembles video frames."""
    global running, latest_frame
    frames_assembly = {}  # frame_id -> {'chunks': {chunk_idx: data}, 'total': N, 'timestamp': t}

    while running:
        try:
            data, addr = sock.recvfrom(65535)
            if not data:
                continue

            packet_type = chr(data[0])
            if packet_type == 'V':  # Video packet
                if len(data) < 9:
                    continue
                
                # Header format:
                # - 'V' (1 byte)
                # - frame_id (4 bytes, uint32)
                # - chunk_idx (2 bytes, uint16)
                # - total_chunks (2 bytes, uint16)
                header = data[:9]
                chunk_data = data[9:]
                _, frame_id, chunk_idx, total_chunks = struct.unpack('!B I H H', header)

                if frame_id not in frames_assembly:
                    frames_assembly[frame_id] = {
                        'chunks': {},
                        'total': total_chunks,
                        'timestamp': time.time()
                    }

                frames_assembly[frame_id]['chunks'][chunk_idx] = chunk_data

                # If all chunks are successfully received, assemble frame
                if len(frames_assembly[frame_id]['chunks']) == total_chunks:
                    chunks_dict = frames_assembly[frame_id]['chunks']
                    full_frame_bytes = b''.join(chunks_dict[i] for i in sorted(chunks_dict.keys()))

                    with frame_lock:
                        latest_frame = full_frame_bytes

                    # Prune old incomplete frames to prevent memory leaks
                    now = time.time()
                    expired = [fid for fid, info in frames_assembly.items() if now - info['timestamp'] > 2.0]
                    for fid in expired:
                        del frames_assembly[fid]

            elif packet_type == 'A':  # Audio packet
                audio_data = data[1:]
                try:
                    audio_queue.put_nowait(audio_data)
                except queue.Full:
                    # Drop audio packets if client buffer overflows to stay real-time
                    pass

        except socket.timeout:
            continue
        except ConnectionResetError:
            # Host went offline or reset.
            continue
        except Exception as e:
            if running:
                print(f"[Client] Socket receiver error: {e}")
            break

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
    print("  Antigravity UDP Streamer - CLIENT")
    print("=" * 60)

    # Prompt user for host IP
    host_ip = input("Enter the Host IP Address: ").strip()
    if not host_ip:
        print("[Client] Error: Host IP cannot be empty.")
        return

    # Setup UDP Socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Bind to ephemeral port
    sock.bind(('0.0.0.0', 0))
    sock.settimeout(1.0)

    # Start network and audio play threads
    ping_thread = threading.Thread(target=ping_host, args=(sock, host_ip), daemon=True)
    recv_thread = threading.Thread(target=receive_packets, args=(sock,), daemon=True)
    audio_thread = threading.Thread(target=audio_play_loop, daemon=True)

    ping_thread.start()
    recv_thread.start()
    audio_thread.start()

    # Create UI Window
    window_name = f"Screen Stream - Connected to {host_ip}"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    
    print(f"[Client] Connecting to host... Open the OpenCV window and press 'q' to exit.")

    try:
        while running:
            frame_data = None
            with frame_lock:
                if latest_frame is not None:
                    frame_data = latest_frame
                    latest_frame = None  # Consume frame

            if frame_data is not None:
                # Decode JPEG and display it
                np_arr = np.frombuffer(frame_data, dtype=np.uint8)
                frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                if frame is not None:
                    cv2.imshow(window_name, frame)

            # Wait key handles OpenCV GUI events (crucial!)
            if cv2.waitKey(10) & 0xFF == ord('q'):
                break

            # Handle case where user closes OpenCV window via 'X' button
            try:
                if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                    break
            except Exception:
                # Handle window property check failure (e.g. if already closed)
                break

    except KeyboardInterrupt:
        print("\n[Client] Exiting...")
    finally:
        running = False
        sock.close()
        cv2.destroyAllWindows()
        print("[Client] Cleanup complete. Goodbye!")

if __name__ == '__main__':
    main()
