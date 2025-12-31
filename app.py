from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import subprocess
import threading
import uuid
from datetime import datetime
import sys

app = Flask(__name__)
app.config["SECRET_KEY"] = "secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# ========= STATE =========
queue = []
now_playing = None
lock = threading.Lock()

# ========= ROUTES =========
@app.route("/")
def index():
    return "DJ Queue Backend is Running 🚀"

@app.route("/api/queue")
def get_queue():
    with lock:
        return jsonify({
            "now_playing": now_playing,
            "queue": queue
        })

@app.route("/api/add", methods=["POST"])
def add_song():
    data = request.json
    url = data.get("url")
    username = data.get("username", "Anonymous")

    if not url:
        return jsonify({"error": "URL required"}), 400

    # get title
    try:
        result = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--print", "%(title)s", "--no-playlist", url],
            capture_output=True,
            text=True,
            timeout=10
        )
        title = result.stdout.strip()
    except:
        title = url[:40]

    song = {
        "id": str(uuid.uuid4())[:8],
        "url": url,
        "title": title,
        "username": username,
        "added_time": datetime.now().strftime("%H:%M:%S")
    }

    with lock:
        queue.append(song)

    socketio.emit("queue_update", queue)
    return jsonify({"success": True, "song": song})

@app.route("/api/next", methods=["POST"])
def next_song():
    global now_playing

    with lock:
        if not queue:
            now_playing = None
            socketio.emit("now_playing", None)
            return jsonify({"message": "Queue empty"})

        song = queue.pop(0)
        now_playing = song

    # extract audio stream URL
    try:
        result = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "-g", "-f", "bestaudio", "--no-playlist", song["url"]],
            capture_output=True,
            text=True,
            timeout=15
        )
        stream_url = result.stdout.strip().split("\n")[0]
    except:
        stream_url = None

    payload = {
        "song": song,
        "stream_url": stream_url
    }

    socketio.emit("now_playing", payload)
    socketio.emit("queue_update", queue)

    return jsonify(payload)

# ========= SOCKET =========
@socketio.on("connect")
def on_connect():
    emit("welcome", {
        "queue": queue,
        "now_playing": now_playing
    })

# ❌ DO NOT USE socketio.run() ON RENDER
# Gunicorn will start the app
