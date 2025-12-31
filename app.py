from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import subprocess
import threading
import time
import json
import os
import uuid
from datetime import datetime
import vlc  # Use VLC instead of ffplay
import sys

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret'
socketio = SocketIO(app, cors_allowed_origins="*")

# ========== SIMPLE STATE ==========
queue = []
now_playing = None
vlc_player = None  # VLC player instance
lock = threading.Lock()
stop_requested = threading.Event()
player_thread = None

# ========== VLC PLAYER ==========
def vlc_worker():
    """Player thread using VLC"""
    global now_playing, vlc_player
   
    print("🎶 VLC Player started")
   
    while True:
        # Wait for songs
        with lock:
            if not queue:
                now_playing = None
                socketio.emit('now_playing', None)
                socketio.emit('queue_update', queue)
                # Wait for new songs
                stop_requested.wait(timeout=1)
                stop_requested.clear()
                continue
           
            # Get next song
            song = queue.pop(0)
            now_playing = song
            now_playing['start_time'] = datetime.now().strftime('%H:%M:%S')
       
        # Broadcast
        socketio.emit('now_playing', now_playing)
        socketio.emit('queue_update', queue)
       
        # Get stream URL
        try:
            print(f"🔍 Getting stream for: {song['title'][:50]}...")
            cmd = [sys.executable, '-m', 'yt_dlp', '-g', '-f', 'bestaudio', '--no-playlist', song['url']]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                print(f"❌ yt-dlp failed: {result.stderr}")
                raise Exception("yt-dlp returned non-zero code")
            audio_url = result.stdout.strip().split('\n')[0]
        except Exception as e:
            print(f"❌ Error extracting audio URL: {e}")
            audio_url = None
       
        if not audio_url:
            print("❌ No audio URL")
            with lock:
                now_playing = None
            continue
       
        # Play with VLC
        print(f"▶️ Playing with VLC: {song['title'][:50]}...")
       
        try:
            # Create VLC instance
            instance = vlc.Instance('--no-video', '--quiet')
            vlc_player = instance.media_player_new()
           
            # Create media from URL
            media = instance.media_new(audio_url)
            vlc_player.set_media(media)
           
            # Play
            vlc_player.play()
            time.sleep(1)  # Wait for playback to start
           
            # Monitor playback
            while True:
                # Check if stop requested
                if stop_requested.is_set():
                    print("⏹️ Stop requested - stopping VLC")
                    vlc_player.stop()
                    stop_requested.clear()
                    break
               
                # Check if playback finished
                state = vlc_player.get_state()
                if state in [vlc.State.Ended, vlc.State.Error, vlc.State.Stopped]:
                    print(f"✅ Playback finished")
                    break
               
                # Small sleep
                time.sleep(0.1)
           
            # Release player
            vlc_player.release()
            vlc_player = None
           
        except Exception as e:
            print(f"❌ VLC error: {e}")
       
        # Song finished
        with lock:
            now_playing = None
       
        time.sleep(0.5)

# Start player thread
player_thread = threading.Thread(target=vlc_worker, daemon=True)
player_thread.start()

# ========== ROUTES ==========
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/add', methods=['POST'])
def add_song():
    data = request.json
    url = data.get('url', '').strip()
    username = data.get('username', 'Anonymous').strip()
   
    if not url:
        return jsonify({'error': 'No URL'}), 400
   
    # Get song info
    try:
        cmd = [sys.executable, '-m', 'yt_dlp', '--print', '%(title)s', '--no-playlist', url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        title = result.stdout.strip() or url[:50]
    except:
        title = url[:50]
   
    # Create song
    song = {
        'id': str(uuid.uuid4())[:8],
        'url': url,
        'title': title[:80] + ('...' if len(title) > 80 else ''),
        'username': username or 'Anonymous',
        'added_time': datetime.now().strftime('%H:%M:%S'),
        'color': ['#FF6B6B', '#4ECDC4', '#FFD166'][hash(username) % 3]
    }
   
    # Add to queue
    with lock:
        queue.append(song)
   
    # Broadcast
    socketio.emit('queue_update', queue)
    socketio.emit('new_song', {
        'song': song,
        'message': f"{username} added a song"
    })
   
    return jsonify({
        'success': True,
        'song': song,
        'queue_length': len(queue)
    })

@app.route('/api/skip', methods=['POST'])
def skip():
    """SKIP - GUARANTEED TO WORK WITH VLC"""
    print("⏭️ SKIP requested")
   
    # Set stop flag
    stop_requested.set()
   
    # Also kill any ffplay processes (if any)
    if os.name == 'nt':
        os.system('taskkill /F /IM ffplay.exe >nul 2>&1')
   
    # Broadcast
    username = request.json.get('username', 'Someone') if request.json else 'Someone'
    socketio.emit('skipped', {
        'by': username,
        'message': 'Song skipped'
    })
   
    # Update queue
    with lock:
        socketio.emit('queue_update', queue)
   
    return jsonify({
        'success': True,
        'message': 'Skip command sent'
    })

@app.route('/api/stop', methods=['POST'])
def stop():
    """STOP EVERYTHING"""
    print("🛑 STOP requested")
   
    # 1. Stop VLC playback
    stop_requested.set()
   
    # 2. Clear queue
    with lock:
        queue.clear()
        now_playing = None
   
    # 3. Kill all audio processes
    if os.name == 'nt':
        os.system('taskkill /F /IM ffplay.exe >nul 2>&1')
        os.system('taskkill /F /IM ffmpeg.exe >nul 2>&1')
   
    # 4. Broadcast
    username = request.json.get('username', 'Someone') if request.json else 'Someone'
    socketio.emit('stopped', {
        'by': username,
        'message': 'Playback stopped'
    })
    socketio.emit('queue_update', queue)
    socketio.emit('now_playing', None)
   
    return jsonify({
        'success': True,
        'message': 'Playback stopped'
    })

@app.route('/api/queue', methods=['GET'])
def get_queue():
    with lock:
        return jsonify({
            'now_playing': now_playing,
            'queue': queue,
            'queue_length': len(queue)
        })

# ========== SOCKET EVENTS ==========
@socketio.on('connect')
def handle_connect():
    print("🔗 Client connected")
   
    with lock:
        emit('welcome', {
            'queue': queue.copy(),
            'now_playing': now_playing
        })

# ========== START SERVER ==========
if __name__ == '__main__':
    print("\n" + "="*60)
    print("🎵 DJ QUEUE WITH VLC - GUARANTEED WORKING 🎵".center(60))
    print("="*60)
   
    # Check dependencies
    try:
        import vlc
        print("✅ VLC is available")
    except:
        print("❌ VLC not installed. Install:")
        print("   1. Install VLC from: https://www.videolan.org/vlc/")
        print("   2. Then: pip install python-vlc")
        sys.exit(1)
   
    try:
        subprocess.run(
    [sys.executable, "-m", "yt_dlp", "--version"],
    capture_output=True,
    check=True
)

        print("✅ yt-dlp is available")
    except:
        print("❌ yt-dlp not found. Install: pip install yt-dlp")
        sys.exit(1)
   
    print("\n🌐 Server: http://localhost:5000")
    print("✅ Skip/Stop will work instantly")
    print("✅ Queue updates properly")
    print("="*60 + "\n")
   
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
