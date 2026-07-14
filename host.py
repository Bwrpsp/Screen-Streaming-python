import os
import socket
import threading
import time
import struct
import mss
import cv2
import numpy as np
import pyaudio
import av
from fractions import Fraction
import ssl
import datetime
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

# Configurations
PORT = 9999
FRAME_RATE = 25     # Target frames per second
AUDIO_RATE = 22050  # Audio sample rate (Hz)
AUDIO_CHANNELS = 1  # 1 = Mono, 2 = Stereo
AUDIO_CHUNK = 1024  # Samples per buffer

# Threading and connection states
running = True
connected_clients = []  # list of client_info dicts: {'socket': s, 'addr': a, 'lock': l}
clients_lock = threading.Lock()
screenshot_warning_printed = False

def generate_self_signed_cert(cert_path="cert.pem", key_path="key.pem"):
    """Programmatically generates a self-signed SSL/TLS certificate and key if they don't exist."""
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return  # Already exists
        
    print("[Host] Generating self-signed SSL certificate...")
    # Generate private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    
    # Generate self-signed certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "California"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "San Francisco"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Antigravity Streamer"),
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])
    
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        private_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    ).not_valid_after(
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)
    ).add_extension(
        x509.SubjectAlternativeName([x509.DNSName("localhost")]),
        critical=False,
    ).sign(private_key, hashes.SHA256())
    
    # Write certificate to file
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
        
    # Write private key to file
    with open(key_path, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))
    print("[Host] SSL certificate generated successfully.")

def get_local_ip():
    """Attempts to find the local IP address by creating a dummy UDP connection."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def get_fallback_frame(width=2560, height=1440):
    """Generates a dynamic mock screen frame to display if screenshot capture fails."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    
    # Subtle blue-to-black gradient
    for y in range(height):
        val = int(50 * (1 - y / height))
        frame[y, :, 0] = val       # B
        frame[y, :, 1] = int(val * 0.4)  # G
        frame[y, :, 2] = int(val * 0.2)  # R
        
    # Title & Text warnings
    cv2.putText(frame, "Host Screen Stream (H.264 TCP SSL/TLS Mode)", (50, 100),
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

def accept_clients(server_sock):
    """Listens for and accepts incoming TCP client connections."""
    global running
    print(f"[Host] TCP server listening for clients on port {PORT}...")
    while running:
        try:
            client_sock, addr = server_sock.accept()
            # Set timeout on client socket so slow sends don't lock host thread
            client_sock.settimeout(5.0)
            
            client_info = {
                'socket': client_sock,
                'addr': addr,
                'lock': threading.Lock()
            }
            with clients_lock:
                connected_clients.append(client_info)
                print(f"[Host] Client connected securely: {addr[0]}:{addr[1]} (Total: {len(connected_clients)})")
        except socket.timeout:
            continue
        except ssl.SSLError as e:
            # Catch client SSL handshake failures without breaking the main loop
            print(f"[Host] SSL Handshake failure with client: {e}")
            continue
        except Exception as e:
            if running:
                print(f"[Host] Socket accept error: {e}")
            break

def cleanup_client(client):
    """Safely closes and removes a disconnected client."""
    with clients_lock:
        if client in connected_clients:
            print(f"[Host] Client disconnected: {client['addr'][0]}:{client['addr'][1]}")
            connected_clients.remove(client)
            try:
                client['socket'].close()
            except Exception:
                pass

def broadcast_packet(msg_type, payload):
    """Sends a framed packet to all connected clients."""
    # Framing header: packet type (1 byte) + payload length (4 bytes uint32)
    header = struct.pack('!B I', ord(msg_type), len(payload))
    packet = header + payload
    
    with clients_lock:
        clients = list(connected_clients)
        
    for client in clients:
        # Run send inside individual client lock to prevent message interleaving
        try:
            with client['lock']:
                client['socket'].sendall(packet)
        except Exception:
            # Clean up client on send failure (e.g. disconnected)
            cleanup_client(client)

def audio_stream_loop():
    """Captures microphone/input audio and broadcasts it to all TCP clients."""
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
        # Sleep if no clients are connected to save CPU
        with clients_lock:
            has_clients = len(connected_clients) > 0
        if not has_clients:
            time.sleep(0.1)
            continue
            
        try:
            # Read audio data from default device
            audio_data = stream.read(AUDIO_CHUNK, exception_on_overflow=False)
            
            # Broadcast audio packet
            broadcast_packet('A', audio_data)
            
            audio_pkt_count += 1
            if audio_pkt_count % 100 == 0:
                print(f"[DEBUG] Sent {audio_pkt_count} audio packets.")
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
    print(f"  Antigravity H.264 Streamer - HOST (TCP SSL/TLS MODE)")
    print(f"  Local IP: {local_ip}")
    print(f"  Port:     {PORT}")
    print("=" * 60)

    # Dynamic Self-Signed Certificate Generation
    cert_file = "cert.pem"
    key_file = "key.pem"
    try:
        generate_self_signed_cert(cert_file, key_file)
    except Exception as e:
        print(f"[Host] ERROR: Failed to prepare SSL/TLS certificates: {e}")
        return

    # Determine screen resolution dynamically before initializing encoder
    with mss.mss() as sct:
        if len(sct.monitors) > 1:
            monitor = sct.monitors[1]
        else:
            monitor = sct.monitors[0]
        screen_w = monitor["width"]
        screen_h = monitor["height"]

    # Calculate dynamic H.264 YUV-compatible dimensions (must be even numbers)
    target_width = 2560
    if screen_w > target_width:
        scale = target_width / screen_w
        target_height = int(screen_h * scale)
    else:
        target_width = screen_w
        target_height = screen_h

    # Ensure even dimensions (divisible by 2) for YUV420P alignment
    target_width = (target_width // 2) * 2
    target_height = (target_height // 2) * 2

    print(f"[Host] Dynamic Stream Resolution set to: {target_width}x{target_height}")

    # Setup TCP Server Socket
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    # Create SSL Context
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(certfile=cert_file, keyfile=key_file)
    
    # Wrap standard socket in SSL for TLS server functionality
    try:
        secure_server_sock = ssl_context.wrap_socket(server_sock, server_side=True)
    except Exception as e:
        print(f"[Host] ERROR: Failed to wrap server socket in SSL: {e}")
        server_sock.close()
        return

    try:
        secure_server_sock.bind(('0.0.0.0', PORT))
        secure_server_sock.listen(5)
    except Exception as e:
        print(f"[Host] ERROR: Could not bind to port {PORT}: {e}")
        secure_server_sock.close()
        return

    secure_server_sock.settimeout(1.0)

    # Start client acceptor thread (passing secure socket)
    acceptor_thread = threading.Thread(target=accept_clients, args=(secure_server_sock,), daemon=True)
    acceptor_thread.start()

    # Start audio stream thread
    audio_thread = threading.Thread(target=audio_stream_loop, daemon=True)
    audio_thread.start()

    # Setup PyAV H.264 Encoder
    codec = av.Codec('h264', 'w')
    encoder = av.CodecContext.create(codec)
    encoder.width = target_width
    encoder.height = target_height
    encoder.pix_fmt = 'yuv420p'
    encoder.time_base = Fraction(1, FRAME_RATE)
    encoder.bit_rate = 15000000  # 15 Mbps for high resolution
    encoder.gop_size = 25  # Force an I-frame every 25 frames (once per second)
    encoder.options = {
        'preset': 'ultrafast',
        'tune': 'zerolatency'
    }
    encoder.open()

    print("[Host] Screen streaming initialized securely. Waiting for connections...")

    # Screen capture stream
    with mss.mss() as sct:
        if len(sct.monitors) > 1:
            monitor = sct.monitors[1]
        else:
            monitor = sct.monitors[0]

        frame_id = 0
        frame_interval = 1.0 / FRAME_RATE

        try:
            while running:
                start_time = time.time()
                
                # Check if there are active clients before capturing screen
                # to reduce CPU overhead when idle
                with clients_lock:
                    has_clients = len(connected_clients) > 0

                if not has_clients:
                    time.sleep(0.1)
                    continue

                try:
                    # Capture screen frame
                    sct_img = sct.grab(monitor)
                    frame = np.array(sct_img)
                    # Convert BGRA to BGR
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                    # Resize to dynamic target dimensions
                    frame = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)
                except Exception as e:
                    if not screenshot_warning_printed:
                        print(f"[Host] WARNING: Screen capture failed ({e}). Using fallback test pattern.")
                        screenshot_warning_printed = True
                    frame = get_fallback_frame(target_width, target_height)

                # Convert numpy array to PyAV VideoFrame
                av_frame = av.VideoFrame.from_ndarray(frame, format='bgr24')
                
                # Encode the frame into H.264 packets
                packets = encoder.encode(av_frame)
                for packet in packets:
                    h264_data = bytes(packet)
                    broadcast_packet('V', h264_data)

                if frame_id % 25 == 0:
                    with clients_lock:
                        clients_count = len(connected_clients)
                    print(f"[DEBUG] Broadcasted secure H.264 frame {frame_id} to {clients_count} clients.")

                frame_id = (frame_id + 1) % 4294967295  # prevent overflow

                # Calculate delay to hit target FPS
                elapsed = time.time() - start_time
                delay = max(0.001, frame_interval - elapsed)
                time.sleep(delay)

        except KeyboardInterrupt:
            print("\n[Host] Shutting down...")
        finally:
            running = False
            
            # Flush encoder
            try:
                packets = encoder.encode(None)
                for packet in packets:
                    h264_data = bytes(packet)
                    broadcast_packet('V', h264_data)
            except Exception:
                pass

            # Close all client connections
            with clients_lock:
                for client in list(connected_clients):
                    try:
                        client['socket'].close()
                    except Exception:
                        pass
                connected_clients.clear()
            
            # Close server socket
            secure_server_sock.close()
            print("[Host] Shutdown complete.")

if __name__ == '__main__':
    main()
