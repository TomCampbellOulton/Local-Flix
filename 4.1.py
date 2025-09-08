import sys, os, re, json, platform, requests, vlc, threading
from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtWidgets import QGraphicsOpacityEffect
from PyQt5.QtCore import QPropertyAnimation, QEasingCurve
from tmdbv3api import TMDb, Movie
from datetime import timedelta
import functools
from math import ceil

CONFIG_FILE = "config.json"
VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm")

THEMES = {
    "Dark": {"window": "#222", "text": "#eee", "panel": "#333", "combo_bg": "#222", "combo_text": "#eee"},
    "Light": {"window": "#f0f0f0", "text": "#000", "panel": "#ddd", "combo_bg": "#fff", "combo_text": "#000"},
    "Netflix": {"window": "#141414", "text": "#e50914", "panel": "#222", "combo_bg": "#222", "combo_text": "#e50914"},
    "Fun": {"window": "#fffae3", "text": "#222", "panel": "#ffd700", "combo_bg": "#ffd700", "combo_text": "#222"}
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE,"r") as f:
                return json.load(f)
        except: return {}
    print("aww")
    return {}
def save_config(d):
    try:
        with open(CONFIG_FILE,"w") as f:
            json.dump(d,f,indent=4)
    except Exception as e: print("Failed saving config:",e)

config = load_config()
TMDB_API_KEY = config.get("tmdb_api_key","")
if not TMDB_API_KEY: raise ValueError("TMDb API key not found in config.json!")

tmdb = TMDb()
tmdb.api_key = TMDB_API_KEY
movie_api = Movie()

CACHE_DIR = ".cache"
METADATA_DIR = os.path.join(CACHE_DIR, "metadata")
POSTER_DIR = os.path.join(CACHE_DIR, "posters")

os.makedirs(METADATA_DIR, exist_ok=True)
os.makedirs(POSTER_DIR, exist_ok=True)

# ---------------- Utility Functions ----------------
def get_cached_metadata(filename):
    path = os.path.join(METADATA_DIR, f"{filename}.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            os.remove(path)
            return None
    return None

def save_metadata(filename, data):
    safe_data = {
        "title": str(data.get("title", "")),
        "overview": str(data.get("overview", "")),
        "genres": [int(g) for g in data.get("genres", [])]
    }
    path = os.path.join(METADATA_DIR, f"{filename}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(safe_data, f, ensure_ascii=False, indent=2)

def get_cached_poster(filename):
    path = os.path.join(POSTER_DIR, f"{filename}.jpg")
    if os.path.exists(path):
        return path
    return None

def is_video_file(filename):
    video_exts = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv"}
    return os.path.splitext(filename)[1].lower() in video_exts

def parse_filename(filename):
    name = os.path.splitext(filename)[0]
    match = re.match(r"[Ss](\d{1,2})[Ee](\d{1,2})\s*-\s*(.*)", name)
    if match:
        season = int(match.group(1))
        episode = int(match.group(2))
        title = match.group(3).strip()
        return season, episode, title
    return 1, 0, name  # fallback
def parse_series_folder(folder_path, video_exts=(".mkv", ".mp4", ".avi")):
    """
    Scans a series folder and groups episodes by season based on SxxExx in filename.
    Returns a dict: {"Season 1": [Episode, ...], "Season 2": [...]}
    """
    seasons = {}
    if not os.path.exists(folder_path):
        return seasons

    for entry in os.scandir(folder_path):
        if entry.is_file() and entry.name.lower().endswith(video_exts):
            # Parse SxxExx - Title.mkv
            name_no_ext = os.path.splitext(entry.name)[0]
            match = re.match(r"[Ss](\d{1,2})[Ee](\d{1,2})\s*-\s*(.*)", name_no_ext)
            if match:
                season_num = int(match.group(1))
                episode_num = int(match.group(2))
                title = match.group(3).strip()
            else:
                # fallback
                season_num = 1
                episode_num = 0
                title = name_no_ext

            season_name = f"Season {season_num}"
            if season_name not in seasons:
                seasons[season_name] = []

            seasons[season_name].append(Episode(title, entry.path))

    # Sort episodes within each season
    for eps in seasons.values():
        eps.sort(key=lambda e: parse_episode_number(e.name))
    return seasons

def parse_episode_number(name):
    """
    Returns episode number for sorting.
    If the original filename had SxxExx format, extract episode number.
    Else fallback to 0.
    """
    match = re.search(r"[Ss]\d{1,2}[Ee](\d{1,2})", name)
    if match:
        return int(match.group(1))
    return 0
def download_poster(poster_path, filename):
    try:
        url = f"https://image.tmdb.org/t/p/original{poster_path}"  # higher resolution
        data = requests.get(url, timeout=8).content
        path = os.path.join(POSTER_DIR, f"{filename}.jpg")
        with open(path, "wb") as f:
            f.write(data)
        return path
    except:
        return None

def list_videos_in_dirs(folders, recursive=True):
    video_exts = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv"}
    videos = []

    for folder in folders:
        if not os.path.exists(folder):
            continue

        if recursive:
            # Walk through subfolders
            for root, dirs, files in os.walk(folder):
                for name in files:
                    if os.path.splitext(name)[1].lower() in video_exts:
                        videos.append(os.path.join(root, name))
        else:
            # Only look at top-level files
            for name in os.listdir(folder):
                full_path = os.path.join(folder, name)
                if os.path.isfile(full_path) and os.path.splitext(name)[1].lower() in video_exts:
                    videos.append(full_path)

    return videos


def get_tmdb_genres():
    url = f"https://api.themoviedb.org/3/genre/movie/list?api_key={TMDB_API_KEY}&language=en-US"
    try:
        response = requests.get(url, timeout=5).json()
        # returns {id: name}
        return {g["id"]: g["name"] for g in response.get("genres", [])}
    except:
        return {}

def fetch_series_metadata(series_name):
    """Fetch metadata + poster for a TV series from TMDB."""
    url = f"https://api.themoviedb.org/3/search/tv"
    params = {
        "api_key": TMDB_API_KEY,
        "query": series_name,
        "language": "en-US",
    }

    try:
        response = requests.get(url, params=params, timeout=8)
        response.raise_for_status()
        data = response.json()

        results = data.get("results", [])
        if not results:
            return None

        best = results[0]  # take the first result

        # ✅ Make sure genres is always a list of ints
        genres = best.get("genre_ids", [])
        if not isinstance(genres, list):
            genres = []

        meta = {
            "title": best.get("name", series_name),
            "overview": best.get("overview", ""),
            "genres": [int(g) for g in genres],
        }

        # Save metadata in cache
        save_metadata(series_name, meta)

        # Download poster if available
        poster_path = best.get("poster_path")
        if poster_path:
            poster_file = download_poster(poster_path, series_name)
            if poster_file:
                meta["poster_path"] = poster_file

        return meta

    except Exception as e:
        print(f"Error fetching series metadata for {series_name}: {e}")
        return None



def clean_filename(filename):
    name = os.path.splitext(filename)[0].replace("."," ").replace("_"," ")
    name = re.sub(r'\b(19|20)\d{2}\b','',name)
    name = re.sub(r'\b(720p|1080p|2160p|480p|HDR|BluRay|WEBRip|x264|H\.?264)\b','',name,flags=re.IGNORECASE)
    return re.sub(r'\s+',' ',name).strip()

def search_movie_by_filename(filename):
    name = clean_filename(filename)
    try:
        results = movie_api.search(name)
        if results: return results[0]
    except Exception as e:
        print(f"TMDb search error for {name}: {e}")
    return None

def format_seconds(sec):
    sec = int(sec)
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_seconds(seconds):
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h:02}:{m:02}:{s:02}"
    return f"{m:02}:{s:02}"



class MouseWatcher(QtCore.QObject):
    movement = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_pos = QtGui.QCursor.pos()
        self.timer = QtCore.QTimer()
        self.timer.setInterval(50)  # check every 50ms
        self.timer.timeout.connect(self.check_mouse)
        self.timer.start()

    def check_mouse(self):
        pos = QtGui.QCursor.pos()
        if pos != self._last_pos:
            self._last_pos = pos
            self.movement.emit()

class VLCPlayerWindow(QtWidgets.QMainWindow):
    closed = QtCore.pyqtSignal()

    def __init__(self, config, theme="Netflix"):
        super().__init__(None)

        # store config immediately
        self.parent_config = config  

        self.setWindowTitle("VLC Player")
        self.resize(1280, 720)

        # init VLC backend after storing config
        self.instance = vlc.Instance()
        self.mplayer = self.instance.media_player_new()

        # --- Video area ---
        self.video_frame = QtWidgets.QFrame(self)
        self.video_frame.setStyleSheet("background:black;")
        self.setCentralWidget(self.video_frame)

        # --- Controls overlay ---
        # Controls bar container
        self.controls = QtWidgets.QWidget(self)
        self.controls.setAutoFillBackground(True)
        if theme == "Dark":
            self.controls.setStyleSheet("background-color: rgba(51,51,51,160);")
        elif theme == "Netflix":
            self.controls.setStyleSheet("background-color: rgba(200,0,0,160);")
        elif theme == "Fun":
            self.controls.setStyleSheet("background-color: rgba(255,215,0,160);")
        elif theme == "Light":
            self.controls.setStyleSheet("background-color: rgba(240,240,240,160);")
        else:
            self.controls.setStyleSheet("background-color: rgba(200,0,0,160);")
        self.controls.setGeometry(0, self.height() - 60, self.width(), 60)

        # Opacity effect
        self.opacity_effect = QtWidgets.QGraphicsOpacityEffect()
        self.controls.setGraphicsEffect(self.opacity_effect)
        self.opacity_effect.setOpacity(1.0)  # start visible

        self.anim = QtCore.QPropertyAnimation(self.opacity_effect, b"opacity")
        self.anim.setEasingCurve(QtCore.QEasingCurve.InOutQuad)

        # Layout inside controls
        self.hbox = QtWidgets.QHBoxLayout(self.controls)
        self.hbox.setContentsMargins(10, 5, 10, 5)

        # Buttons + widgets (play, slider, volume, etc.)
        self.play_btn = QtWidgets.QPushButton("⏸ Pause")
        self.play_btn.clicked.connect(self.toggle_play)
        self.position_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.position_slider.setRange(0, 1000)
        self.position_slider.sliderMoved.connect(self.set_position)
        self.time_label = QtWidgets.QLabel("00:00 / 00:00")
        self.volume_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(50)
        self.volume_slider.valueChanged.connect(lambda val: self.mplayer.audio_set_volume(val))
        self.subs_combo = QtWidgets.QComboBox()
        self.subs_combo.addItem("Subtitles: Off", -1)
        self.audio_combo = QtWidgets.QComboBox()
        self.back_btn = QtWidgets.QPushButton("⬅ Back")
        self.back_btn.clicked.connect(self.close)
        for w in [self.play_btn, self.position_slider, self.time_label,
                  QtWidgets.QLabel("Vol"), self.volume_slider,
                  self.subs_combo, self.audio_combo, self.back_btn]:
            self.hbox.addWidget(w)
        self.hbox.addStretch()

        # Timer to auto-hide controls after inactivity
        self.inactivity_timer = QtCore.QTimer(self)
        self.inactivity_timer.setInterval(2000)  # 2 seconds of inactivity
        self.inactivity_timer.timeout.connect(self.fade_out_controls)
        
        # Timer for UI updates (seek slider, etc.)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(500)  # update every 0.5s
        self.timer.timeout.connect(self.update_ui)

        # Enable mouse tracking
        self.setMouseTracking(True)
        self.video_frame.setMouseTracking(True)
        self.installEventFilter(self)
        
        # Global mouse watcher
        self.mouse_watcher = MouseWatcher(self)
        self.mouse_watcher.movement.connect(self.on_global_mouse_move)


        # Initially block signals
        self.audio_combo.blockSignals(True)
        self.subs_combo.blockSignals(True)
        
        # Populate default items
        self.audio_combo.addItem("Audio: None", -1)
        self.subs_combo.addItem("Subtitles: Off", -1)

        # Now connect safely after population
        self.audio_combo.currentIndexChanged.connect(self.change_audio)
        self.subs_combo.currentIndexChanged.connect(self.change_subtitles)

        # Unblock signals
        self.audio_combo.blockSignals(False)
        self.subs_combo.blockSignals(False)

        
        # Play/Pause
        self.shortcut_play_pause = QtWidgets.QShortcut(QtGui.QKeySequence("Space"), self)
        self.shortcut_play_pause.activated.connect(self.toggle_play_pause)

        # Skip forward/back
        self.shortcut_forward = QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Right), self)
        self.shortcut_forward.activated.connect(lambda: self.skip_time(10000))  # +10s

        self.shortcut_back = QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Left), self)
        self.shortcut_back.activated.connect(lambda: self.skip_time(-10000))  # -10s

        # Mute Toggle
        self.shortcut_mute = QtWidgets.QShortcut(QtGui.QKeySequence("M"), self)
        self.shortcut_mute.activated.connect(self.toggle_mute)
        
        # Fullscreen toggle
        self.shortcut_fullscreen = QtWidgets.QShortcut(QtGui.QKeySequence("F"), self)
        self.shortcut_fullscreen.activated.connect(self.toggle_fullscreen)
        if theme == "Dark":
            self.controls.setStyleSheet(f"background-color: {THEMES[theme]['panel']};")
        elif theme == "Netflix":
            self.controls.setStyleSheet(f"background-color: {THEMES[theme]['panel']};")
        elif theme == "Fun":
            self.controls.setStyleSheet(f"background-color: {THEMES[theme]['panel']};")
        elif theme == "Light":
            self.controls.setStyleSheet(f"background-color: {THEMES[theme]['panel']};")
        else:
            self.controls.setStyleSheet(f"background-color: {THEMES['Netflix']['panel']};")
        

    def toggle_play_pause(self):
        print("?")
        if self.mplayer.is_playing():
            self.mplayer.pause()
        else:
            self.mplayer.play()

    def skip_time(self, ms):
        try:
            cur = self.mplayer.get_time()
            self.mplayer.set_time(max(0, cur + ms))
        except:
            pass
        
    def on_global_mouse_move(self):
        self.fade_in_controls()
        self.inactivity_timer.start()  # restart timer

    def toggle_mute(self):
        self.mplayer.audio_toggle_mute()

    # --- Fade controls ---
    def fade_in_controls(self):
        self.anim.stop()
        self.anim.setDuration(200)
        self.anim.setStartValue(self.opacity_effect.opacity())
        self.anim.setEndValue(1.0)
        self.anim.start()
        self.inactivity_timer.start()  # restart timer

    def fade_out_controls(self):
        self.anim.stop()
        self.anim.setDuration(400)
        self.anim.setStartValue(self.opacity_effect.opacity())
        self.anim.setEndValue(0.0)
        self.anim.start()

        
    def resizeEvent(self, event):
        """Keep controls docked at bottom."""
        self.controls.setGeometry(0, self.height() - 60, self.width(), 60)
        super().resizeEvent(event)

    # --- Detect mouse movement ---
    def mouseMoveEvent(self, event):
        self.fade_in_controls()
        self.inactivity_timer.start()   # reset timer
        super().mouseMoveEvent(event)

    # --- Event filter for global mouse detection ---
    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.MouseMove:
            self.fade_in_controls()
            self.inactivity_timer.start()   # reset timer
        return super().eventFilter(obj, event)


    # --- Fullscreen toggle ---
    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_F:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()
        else:
            super().keyPressEvent(event)

    # --- Fullscreen toggle ---
    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.is_fullscreen = False
        else:
            self.showFullScreen()
            self.is_fullscreen = True
                
    def update_ui(self):
        """Update slider and time label every 0.5s."""
        if not self.mplayer:
            return
        try:
            # Position slider
            pos = self.mplayer.get_position()
            if pos >= 0:
                self.position_slider.blockSignals(True)
                self.position_slider.setValue(int(pos * 1000))
                self.position_slider.blockSignals(False)

            # Time label
            length_ms = self.mplayer.get_length()
            current_ms = self.mplayer.get_time()
            if length_ms > 0 and current_ms >= 0:
                total_sec = int(length_ms / 1000)
                current_sec = int(current_ms / 1000)
                self.time_label.setText(f"{format_seconds(current_sec)} / {format_seconds(total_sec)}")
        except Exception as e:
            print("update_ui error:", e)


            
    # --- Play a video ---
    def play(self, path: str):
        if not os.path.exists(path):
            QtWidgets.QMessageBox.warning(self, "File not found", f"Cannot find: {path}")
            return

        # Load media
        media = self.instance.media_new(path)
        self.mplayer.set_media(media)

        # Set video output window
        winid = int(self.video_frame.winId())
        if platform.system() == "Windows":
            self.mplayer.set_hwnd(winid)
        elif platform.system() == "Darwin":
            try: self.mplayer.set_nsobject(winid)
            except: pass
        else:
            self.mplayer.set_xwindow(winid)
        self.mplayer.play()  # start playback
        self.fade_in_controls()  # show initially
        self.inactivity_timer.start()  # begin tracking inactivity

        QtCore.QTimer.singleShot(1000, lambda: self.mplayer.video_set_spu(-1))

        # Wait a short delay to let VLC initialize
        def seek_and_load():
            # Seek to last watched position if available
            last_pos = self.parent_config.get("positions", {}).get(path)
            if last_pos is not None:
                try:
                    self.mplayer.set_time(int(last_pos * 1000))  # VLC expects milliseconds
                except: pass

            # Load audio/subtitle tracks
            self.load_tracks()

            # Start UI updates
            self.timer.start()

        QtCore.QTimer.singleShot(500, seek_and_load)

    def _bind_and_start(self, path: str):
        try:
            print("We be bound")
            winid = int(self.video_frame.winId())
            sysplat = platform.system()

            if sysplat == "Windows":
                self.mplayer.set_hwnd(winid)
            elif sysplat == "Darwin":
                from ctypes import c_void_p
                self.mplayer.set_nsobject(c_void_p(winid))
            else:  # Linux
                self.mplayer.set_xwindow(winid)

            self.mplayer.play()
            self.timer = QtCore.QTimer(self)
            self.timer.setInterval(500)  # 0.5s
            self.timer.timeout.connect(self.update_ui)
            QtCore.QTimer.singleShot(1000, self.populate_subtitles)
            QtCore.QTimer.singleShot(1000, self.populate_audio)
            QtCore.QTimer.singleShot(1500, self.load_tracks)

            # Ask resume if needed
            last_pos = self.parent_config.get("positions", {}).get(path) if self.parent_config else None
            if last_pos:
                print("Offering resume")
                self.mplayer.set_time(int(last_pos * 1000))  # VLC expects milliseconds
                reply = QtWidgets.QMessageBox.question(
                    self, "Resume Playback",
                    f"Resume from {last_pos//60000}:{(last_pos//1000)%60:02d} ?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
                )
                if reply == QtWidgets.QMessageBox.Yes:
                    print("Trying")
                    QtCore.QTimer.singleShot(1500, lambda: self.mplayer.set_time(last_pos))

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Playback Error", f"Failed to start video:\n{e}")

    # --- Track loading ---
    def load_tracks(self):
        # --- Subtitles ---
        self.subs_combo.blockSignals(True)
        self.subs_combo.clear()
        self.subs_combo.addItem("Subtitles: Off", -1)
        try:
            subs = self.mplayer.video_get_spu_description()
            if subs:
                for track_id, name in subs:
                    name_str = name.decode() if isinstance(name, bytes) else str(name)
                    self.subs_combo.addItem(name_str, track_id)
        except Exception as e:
            print("Error loading subtitles:", e)
        self.subs_combo.blockSignals(False)

        # --- Audio tracks ---
        self.audio_combo.blockSignals(True)
        self.audio_combo.clear()
        try:
            audios = self.mplayer.audio_get_track_description()
            if audios:
                for track_id, name in audios:
                    name_str = name.decode() if isinstance(name, bytes) else str(name)
                    self.audio_combo.addItem(name_str, track_id)
        except Exception as e:
            print("Error loading audio tracks:", e)
        self.audio_combo.blockSignals(False)



    # --- Playback controls ---
    def toggle_play(self):
        if self.mplayer.is_playing():
            self.mplayer.pause()
            self.play_btn.setText("▶ Play")
        else:
            self.mplayer.play()
            self.timer = QtCore.QTimer(self)
            self.timer.setInterval(500)  # 0.5s
            self.timer.timeout.connect(self.update_ui)
            QtCore.QTimer.singleShot(1000, self.populate_subtitles)
            QtCore.QTimer.singleShot(1000, self.populate_audio)
            self.play_btn.setText("⏸ Pause")

    def set_position(self, pos):
        if self.mplayer:
            self.mplayer.set_position(pos / 1000.0)  # VLC expects 0.0–1.0
        try:
            length = self.mplayer.get_length()
            if length > 0:
                new_time = int((pos / self.position_slider.maximum()) * length)
                self.mplayer.set_time(new_time)
        except Exception as e:
            print("set_position error:", e)
            
    def update_slider(self):
        try:
            pos = int(self.mplayer.get_position() * 1000)
            if 0 <= pos <= 1000:
                self.position_slider.setValue(pos)
            length = self.mplayer.get_length()/1000
            current = self.mplayer.get_time()/1000
            self.time_label.setText(f"{format_seconds(current)} / {format_seconds(length)}")
        except: pass

    def change_audio(self, index):
        try:
            if not self.mplayer:
                return
            track_id = self.audio_combo.itemData(index)
            if track_id is not None:
                self.mplayer.audio_set_track(track_id)
        except Exception as e:
            print("change_audio error:", e)
            
    def populate_subtitles(self):
        """Populate the subtitle combo after playback starts."""
        try:
            self.subs_combo.blockSignals(True)
            self.subs_combo.clear()
            self.subs_combo.addItem("Subtitles: Off", -1)

            spu_tracks = self.mplayer.video_get_spu_description()
            if spu_tracks:
                for track in spu_tracks:
                    if isinstance(track, tuple) and len(track) == 2:
                        tid, name = track
                        self.subs_combo.addItem(str(name), tid)

                # Select current SPU
                current_spu = self.mplayer.video_get_spu()
                idx = self.subs_combo.findData(current_spu)
                if idx >= 0:
                    self.subs_combo.setCurrentIndex(idx)

            self.subs_combo.blockSignals(False)
        except Exception as e:
            print("populate_subtitles error:", e)
            
    def populate_audio(self):
        try:
            self.audio_combo.blockSignals(True)
            self.audio_combo.clear()
            audios = self.mplayer.audio_get_track_description()
            if audios:
                for track in audios:
                    if isinstance(track, tuple) and len(track) == 2:
                        tid, name = track
                        self.audio_combo.addItem(str(name), tid)

                current_audio = self.mplayer.audio_get_track()
                idx = self.audio_combo.findData(current_audio)
                if idx >= 0:
                    self.audio_combo.setCurrentIndex(idx)
            self.audio_combo.blockSignals(False)
        except Exception as e:
            print("populate_audio error:", e)
            
    def change_subtitles(self, index):
        try:
            if not self.mplayer:
                return
            track_id = self.subs_combo.itemData(index)
            if track_id is not None:
                self.mplayer.video_set_spu(track_id)
        except Exception as e:
            print("change_subtitles error:", e)

    def toggle_mute(self):
        try:
            current = self.mplayer.audio_get_mute()
            self.mplayer.audio_set_mute(not current)
            if self.mplayer.audio_get_mute():
                self.back_btn.setText("⬅ Back (Muted)")
            else:
                self.back_btn.setText("⬅ Back")
            self.fade_in_controls()
        except: pass

    def show_subs_popup(self):
        if self.subs_combo.count() > 0:
            self.subs_combo.showPopup()
            self.fade_in_controls()

    def show_audio_popup(self):
        if self.audio_combo.count() > 0:
            self.audio_combo.showPopup()
            self.fade_in_controls()

    # --- Cleanup ---
    def stop_and_release(self):
        try: self.mplayer.stop(); self.mplayer.release(); self.mplayer=None
        except: pass
        try: self.timer.stop()
        except: pass
        try: 
            if self.instance: self.instance.release(); self.instance=None
        except: pass

    def closeEvent(self, event):
        try:
            pos = self.mplayer.get_time()
            if pos > 0 and self._current_media_path:
                self.parent_config.setdefault("positions", {})[self._current_media_path] = pos
                save_config(self.parent_config)
        except:
            pass

        self.stop_and_release()
        self.closed.emit()
        event.accept()

# ---------------- Video Model ----------------
class VideoItem(QtCore.QObject):
    updated = QtCore.pyqtSignal()  # emitted when metadata/poster updated

    def __init__(self, path, movie=None):
        super().__init__()
        self.path = path
        self.filename = os.path.splitext(os.path.basename(path))[0]
        self.title = os.path.basename(path)
        self.overview = "No description available."
        self.genres = []
        self.poster_path = None
        self.pixmap = None

        # Load cached data
        metadata = get_cached_metadata(self.filename)
        poster_file = get_cached_poster(self.filename)
        if metadata:
            self.title = metadata.get("title", self.title)
            self.overview = metadata.get("overview", self.overview)
            self.genres = metadata.get("genres", [])
        if poster_file:
            self.poster_path = poster_file

        # Start async fetch if needed
        threading.Thread(target=self._fetch_online, args=(movie,), daemon=True).start()

    def _fetch_online(self, movie=None):
        # movie can be None, we fetch by filename
        if movie is None:
            movie = search_movie_by_filename(os.path.basename(self.path))  # your function
        if not movie:
            return

        # Update metadata
        self.title = movie.title or self.title
        self.overview = movie.overview or self.overview
        if hasattr(movie, "genre_ids"):
            self.genres = [int(g) for g in movie.genre_ids]

        save_metadata(self.filename, {
            "title": self.title,
            "overview": self.overview,
            "genres": self.genres
        })

        # Download poster if missing
        if not self.poster_path and getattr(movie, "poster_path", None):
            local_poster = download_poster(movie.poster_path, self.filename)
            if local_poster:
                self.poster_path = local_poster

        # Notify GUI to update
        self.updated.emit()
        
    def get_path(self):
        return self.path

class SeriesItem(QtCore.QObject):
    updated = QtCore.pyqtSignal()  # emitted when poster/metadata updated

    def __init__(self, folder_path):
        super().__init__()
        self.folder_path = folder_path
        self.title = os.path.basename(folder_path)
        self.overview = "No description available."
        self.genres = []
        self.poster_path = None
        self.pixmap = None

        # Load cached metadata/poster
        metadata = get_cached_metadata(self.title)
        poster_file = get_cached_poster(self.title)
        if metadata:
            self.title = metadata.get("title", self.title)
            self.overview = metadata.get("overview", self.overview)
            self.genres = metadata.get("genres", [])
        if poster_file:
            self.poster_path = poster_file

        # Fetch metadata asynchronously
        threading.Thread(target=self.fetch_metadata, daemon=True).start()
        
    def get_path(self):
        return self.folder_path
    
    def fetch_metadata(self):
        # Search TMDb by folder name
        try:
            from tmdbv3api import TMDb, TV
            tmdb = TMDb()
            tmdb.api_key = TMDB_API_KEY
            tv_api = TV()

            results = tv_api.search(self.title)
            if results:
                tv = results[0]
                self.title = tv.name or self.title
                self.overview = tv.overview or self.overview
                self.genres = [g for g in getattr(tv, "genre_ids", [])]

                # Save metadata
                save_metadata(self.title, {
                    "title": self.title,
                    "overview": self.overview,
                    "genres": self.genres
                })

                # Download poster
                if getattr(tv, "poster_path", None):
                    local_poster = download_poster(tv.poster_path, self.title)
                    if local_poster:
                        self.poster_path = local_poster

                # Notify GUI
                self.updated.emit()
        except Exception as e:
            print(f"Error fetching series metadata for {self.title}: {e}")

    def _scan_episodes(self):
        episodes = []
        for entry in os.scandir(self.path):
            if entry.is_file() and is_video_file(entry.name):
                season, episode = self._parse_episode(entry.name)
                episodes.append((season, episode, entry.path))
        episodes.sort(key=lambda x: (x[0], x[1]))
        return episodes

    def _parse_episode(self, filename):
        match = re.search(r"[Ss](\d{1,2})[Ee](\d{1,2})", filename)
        if match:
            return int(match.group(1)), int(match.group(2))
        return 0, 0


"""
class SeriesWindow(QtWidgets.QWidget):
    def __init__(self, series_item, config, parent=None):
        super().__init__(parent)
        self.series_item = series_item
        self.config = config
        self.setWindowTitle(series_item.title)
        self.resize(900, 700)

        layout = QtWidgets.QVBoxLayout(self)

        # --- Poster & Overview ---
        if self.series_item.poster_path and os.path.exists(self.series_item.poster_path):
            poster = QtWidgets.QLabel()
            pix = QtGui.QPixmap(self.series_item.poster_path).scaledToWidth(
                300, QtCore.Qt.SmoothTransformation
            )
            poster.setPixmap(pix)
            poster.setAlignment(QtCore.Qt.AlignHCenter)
            layout.addWidget(poster)

        if hasattr(self.series_item, "overview") and self.series_item.overview:
            overview = QtWidgets.QLabel(self.series_item.overview)
            overview.setWordWrap(True)
            overview.setStyleSheet("font-size: 14px; margin: 10px;")
            layout.addWidget(overview)
        # -------------------------

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        container = QtWidgets.QWidget()
        scroll.setWidget(container)
        vbox = QtWidgets.QVBoxLayout(container)

        # Group by season
        seasons = {}
        for season, episode, path in series_item.episodes:
            if season not in seasons:
                label = QtWidgets.QLabel(f"Season {season}")
                label.setStyleSheet("font-size: 18px; font-weight: bold; margin-top: 12px;")
                vbox.addWidget(label)

                lw = QtWidgets.QListWidget()
                lw.setViewMode(QtWidgets.QListView.IconMode)
                lw.setIconSize(QtCore.QSize(160, 240))
                lw.setResizeMode(QtWidgets.QListView.Adjust)
                lw.setSpacing(10)
                vbox.addWidget(lw)
                lw.itemDoubleClicked.connect(self.on_episode_double_clicked)

                seasons[season] = lw

            # Make an episode item with thumbnail
            ep_item = QtWidgets.QListWidgetItem(f"Ep {episode}")
            ep_item.setData(QtCore.Qt.UserRole, path)

            # Try to use frame/poster if available
            thumb = self.extract_thumbnail(path)
            if thumb:
                ep_item.setIcon(QtGui.QIcon(thumb))
            else:
                ep_item.setIcon(QtGui.QIcon(":/icons/video.png"))

            seasons[season].addItem(ep_item)

        vbox.addStretch(1)
        layout.addWidget(scroll)

    def on_episode_double_clicked(self, item):
        path = item.data(QtCore.Qt.UserRole)
        if path:
            player = VLCPlayerWindow(self.config)
            player.play(path)
            player.show()

    def extract_thumbnail(self, path):
        ""Optional: extract frame thumbnail from video (placeholder now).""
        # TODO: use ffmpeg or QPixmap fallback
        return None"""

class EpisodeItem(QtCore.QObject):
    updated = QtCore.pyqtSignal()  # emitted when poster is downloaded

    def __init__(self, path, season, filename, episode_number=None):
        super().__init__()
        self.path = path
        self.season = season
        self.filename = filename
        self.episode_number = episode_number or filename
        self.poster_path = None
        self.pixmap = None

        # Optionally fetch metadata/poster asynchronously
        threading.Thread(target=self.fetch_metadata, daemon=True).start()

    def fetch_metadata(self):
        # Search TMDb using series title + season/episode if possible
        try:
            from tmdbv3api import TMDb, TV
            tmdb = TMDb()
            tmdb.api_key = TMDB_API_KEY
            tv_api = TV()

            # Extract series name from folder
            series_name = os.path.basename(os.path.dirname(self.path))
            results = tv_api.search(series_name)
            if not results:
                return

            tv_show = results[0]
            # TMDb season/episode search
            season_num = int(self.season.replace("Season ", "")) if "Season" in self.season else 1
            episodes = tv_api.season(tv_show.id, season_num)
            for ep in episodes.episodes:
                if ep.name.lower() in self.filename.lower() or str(ep.episode_number) in self.filename:
                    if getattr(ep, "still_path", None):
                        local_poster = download_poster(ep.still_path, f"{series_name}_{season_num}_{ep.episode_number}")
                        if local_poster:
                            self.poster_path = local_poster
                            self.updated.emit()
                    break
        except Exception as e:
            print(f"Error fetching episode metadata for {self.filename}: {e}")

class Episode:
    def __init__(self, title, path, poster_path=None):
        self.name = title
        self.path = path
        self.poster_path = poster_path if poster_path and os.path.exists(poster_path) else "video.png"


class FlowLayout(QtWidgets.QLayout):
    """A Qt flow layout that wraps child widgets automatically."""
    def __init__(self, parent=None, margin=0, spacing=10):
        super().__init__(parent)
        self.item_list = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def addItem(self, item):
        self.item_list.append(item)

    def count(self):
        return len(self.item_list)

    def itemAt(self, index):
        return self.item_list[index] if index < len(self.item_list) else None

    def takeAt(self, index):
        if 0 <= index < len(self.item_list):
            return self.item_list.pop(index)
        return None

    def expandingDirections(self):
        return QtCore.Qt.Orientations(QtCore.Qt.Horizontal | QtCore.Qt.Vertical)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self.doLayout(QtCore.QRect(0, 0, width, 0), testOnly=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.doLayout(rect, testOnly=False)

    def sizeHint(self):
        # Recommend the minimum size to fit all items
        return self.minimumSize()

    def minimumSize(self):
        size = QtCore.QSize()
        for item in self.item_list:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QtCore.QSize(margins.left() + margins.right(),
                             margins.top() + margins.bottom())
        return size

    def doLayout(self, rect, testOnly):
        x, y, lineHeight = rect.x(), rect.y(), 0
        for item in self.item_list:
            wid = item.widget()
            if not wid.isVisible():
                continue
            hint = wid.sizeHint()
            if x + hint.width() > rect.right() and lineHeight > 0:
                x = rect.x()
                y += lineHeight + self.spacing()
                lineHeight = 0
            if not testOnly:
                item.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), hint))
            x += hint.width() + self.spacing()
            lineHeight = max(lineHeight, hint.height())
        return y + lineHeight - rect.y()
    
# ---------------------------
# EpisodeCard
# ---------------------------
class EpisodeCard(QtWidgets.QFrame):
    def __init__(self, episode, open_episode_func, base_width=130, theme="Dark"):
        super().__init__()
        self.episode = episode
        self.open_episode_func = open_episode_func
        self.base_width = base_width
        self.zoom_factor = 1.0
        self.spacing = 5

        self.setCursor(QtCore.Qt.PointingHandCursor)
        print(theme)
        
        if theme == "Dark":
            self.setStyleSheet("border-radius:6px; background-color:#333;")
        elif theme == "Netflix":
            self.setStyleSheet(f"border-radius:6px; background-color:{THEMES['Netflix']['panel']};")
        elif theme == "Fun":
            self.setStyleSheet(f"border-radius:6px; background-color:{THEMES['Fun']['panel']};")
        elif theme == "Light":
            self.setStyleSheet(f"border-radius:6px; background-color:{THEMES['Light']['panel']};")
        else:
            self.setStyleSheet("border-radius:6px; background-color:#333;")
            
        self.setMinimumSize(self.base_width, 150)
        self.setMaximumWidth(self.base_width*2)

        # Main layout
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # Thumbnail placeholder
        pixmap = QtGui.QPixmap(1, 1)
        if theme == "Dark":
            pixmap.fill(QtGui.QColor("#555"))
        elif theme == "Netflix":
            pixmap.fill(QtGui.QColor(THEMES['Netflix']['combo_bg']))
        elif theme == "Fun":
            pixmap.fill(QtGui.QColor(THEMES['Fun']['combo_bg']))
        elif theme == "Light":
            pixmap.fill(QtGui.QColor(THEMES['Light']['combo_bg']))
        else:
            pixmap.fill(QtGui.QColor("#555"))
        self.thumb_label = QtWidgets.QLabel()
        self.thumb_label.setPixmap(pixmap)
        self.thumb_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.thumb_label)

        # Episode text
        text = getattr(episode, "name", str(episode))
        self.text_label = QtWidgets.QLabel(text)
        self.text_label.setWordWrap(True)
        self.text_label.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)
        if theme == "Dark":
            self.text_label.setStyleSheet("color:white; font-size:18px;")
        elif theme == "Netflix":
            self.text_label.setStyleSheet(f"color:{THEMES['Netflix']['text']}; font-size:18px;")
        elif theme == "Fun":
            self.text_label.setStyleSheet(f"color:{THEMES['Fun']['text']}; font-size:18px;")
        elif theme == "Light":
            self.text_label.setStyleSheet(f"color:{THEMES['Light']['text']}; font-size:18px;")
        else:
            self.text_label.setStyleSheet("color:white; font-size:18px;")
        layout.addWidget(self.text_label)
        print("Added text label")

        # Click handling
        self.mousePressEvent = lambda event: self.open_episode_func(getattr(self.episode, "path", str(self.episode)))

    def set_thumbnail(self, path):
        pix = QtGui.QPixmap(path)
        if not pix.isNull():
            pix = pix.scaled(150, 100, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            self.thumb_label.setPixmap(pix)

# ---------------------------
# SeasonWidget
# ---------------------------
class SeasonWidget(QtWidgets.QWidget):
    def __init__(self, episodes, open_episode_func, base_width=130, theme="Dark"):
        super().__init__()
        self.episodes = episodes
        self.open_episode_func = open_episode_func
        self.base_width = base_width
        self.zoom_factor = 1.0
        self.spacing = 10

        self.theme = theme

        self.grid = QtWidgets.QGridLayout(self)
        self.grid.setContentsMargins(0,0,0,0)
        self.grid.setSpacing(self.spacing)

        self.cards = None

    def showEvent(self, event):
        super().showEvent(event)
        if self.cards is None:
            self.lazy_create_cards()
            
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.rearrange_cards()

    def lazy_create_cards(self):
        if self.cards is not None:
            return
        self.cards = [EpisodeCard(e, self.open_episode_func, self.base_width, self.theme) for e in self.episodes]
        self.rearrange_cards()

    def rearrange_cards(self):
        if not self.cards or not self.isVisible():
            return

        card_w = max(50, int(self.base_width * self.zoom_factor))
        width = max(100, self.width())

        # Number of columns that can fit
        columns = max(1, width // (card_w + self.spacing))
        if hasattr(self, "_last_columns") and self._last_columns == columns:
            return
        self._last_columns = columns

        # Clear old layout
        for i in reversed(range(self.grid.count())):
            w = self.grid.itemAt(i).widget()
            if w:
                self.grid.removeWidget(w)
                w.setParent(None)

        # Disable column stretching
        for col in range(columns):
            self.grid.setColumnStretch(col, 0)

        self.grid.setHorizontalSpacing(self.spacing)
        self.grid.setVerticalSpacing(self.spacing)

        # Add cards row by row
        for idx, card in enumerate(self.cards):
            row = idx // columns
            col = idx % columns
            card.setFixedWidth(card_w)
            self.grid.addWidget(card, row, col, alignment=QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)

        # Fill the last row with visible placeholders for testing
        remainder = len(self.cards) % columns
        if remainder != 0:
            for col in range(remainder, columns):
                spacer = QtWidgets.QLabel()
                spacer.setFixedSize(card_w, card_w)  # square placeholder
                #spacer.setStyleSheet("background-color: rgba(200, 0, 0, 0.5); border: 1px dashed #000;")
                spacer.setAlignment(QtCore.Qt.AlignCenter)
                #spacer.setText("F")  # visible text for testing
                row = len(self.cards) // columns
                self.grid.addWidget(spacer, row, col)





# ---------------------------
# CollapsibleSeasonWidget
# ---------------------------
class CollapsibleSeasonWidget(QtWidgets.QWidget):
    def __init__(self, season_name, episodes, open_episode_func, theme="Dark"):
        super().__init__()
        self.expanded = True

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(2)

        self.theme = theme

        # Toggle button
        self.toggle_btn = QtWidgets.QPushButton(f"▼ {season_name}")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(True)
        self.toggle_btn.clicked.connect(self.toggle)
        # Set buttons to be colour coded to the theme
        print("Trying out the theme")
        if theme == "Dark":
            self.toggle_btn.setStyleSheet(
                "text-align:left; font-size:16px; color:#ccc; background:#222; padding:6px;"
            )
        elif theme == "Light":
            self.toggle_btn.setStyleSheet(f"text-align:left; font-size:16px; color:{THEMES['Light']['text']}; background:{THEMES['Light']['panel']}; padding:6px;")

        elif theme == "Fun":
            self.toggle_btn.setStyleSheet(f"text-align:left; font-size:16px; color:{THEMES['Fun']['text']}; background:{THEMES['Fun']['panel']}; padding:6px;")

        elif theme == "Netflix":
            print("Theme of netflix")
            self.toggle_btn.setStyleSheet(f"text-align:left; font-size:16px; color:{THEMES['Netflix']['text']}; background:{THEMES['Netflix']['panel']}; padding:6px;")
        else:
            print("Invalid colour scheme, using Dark as default")
            print(theme)
            self.toggle_btn.setStyleSheet(
                "text-align:left; font-size:16px; color:#ccc; background:#222; padding:6px;"
            )
        layout.addWidget(self.toggle_btn)

        # Season content
        print("Passing in")
        self.season_widget = SeasonWidget(episodes, open_episode_func, theme = self.theme)
        print("Passed out", self.theme)
        layout.addWidget(self.season_widget)

    def toggle(self):
        self.expanded = not self.expanded
        self.season_widget.setVisible(self.expanded)
        arrow = "▼" if self.expanded else "▶"
        self.toggle_btn.setText(f"{arrow} {self.toggle_btn.text()[2:]}")



class SeriesViewerWindow(QtWidgets.QWidget):
    def __init__(self, series_item, theme="Dark"):
        super().__init__()
        self.series_item = series_item
        self.setWindowTitle(series_item.title)
        self.resize(800, 600)

        self.main_layout = QtWidgets.QVBoxLayout(self)

        # Banner
        banner = QtWidgets.QLabel(f"<h2>{series_item.title}</h2>")
        banner.setAlignment(QtCore.Qt.AlignCenter)
        self.main_layout.addWidget(banner)

        # Scroll area for episodes
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        self.main_layout.addWidget(scroll)

        self.container = QtWidgets.QWidget()
        self.container_layout = QtWidgets.QVBoxLayout(self.container)
        self.container_layout.setAlignment(QtCore.Qt.AlignTop)  # top alignment only
        self.container_layout.setSpacing(15)

        self.theme = theme
        
        scroll.setWidget(self.container)

        self.populate_episodes()
        
    def populate_episodes(self, root_folder=None):
        # Determine root folder
        if root_folder is None:
            root_folder = self.series_item.get_path()
        
        # Clear previous content
        for i in reversed(range(self.container_layout.count())):
            w = self.container_layout.itemAt(i).widget()
            if w:
                w.setParent(None)

        series_name = os.path.basename(root_folder)
        header_series = QtWidgets.QLabel(f"<h2>{series_name}</h2>")
        header_series.setAlignment(QtCore.Qt.AlignCenter)
        header_series.setStyleSheet("margin:12px 0px; font-size:20px; color:#eee;")
        self.container_layout.addWidget(header_series)

        # Parse episodes inside this folder
        parsed_seasons = parse_series_folder(root_folder)

        for season_name, episodes in sorted(parsed_seasons.items()):
            collapsible = CollapsibleSeasonWidget(season_name, episodes, self.open_episode, theme=self.theme)
            self.container_layout.addWidget(collapsible)

        self.container_layout.addStretch(1)



    def open_episode(self, path):
        # Open VLC player for this episode
        player = VLCPlayerWindow(config, self.theme)
        player.play(path)
        player.show()

    def update_episode_icon(self, tree_item, episode_item):
        if episode_item.poster_path:
            pixmap = QtGui.QPixmap(episode_item.poster_path)
            icon = QtGui.QIcon(pixmap)
            tree_item.setIcon(0, icon)

    def on_episode_double_click(self, item, column):
        ep = item.data(0, QtCore.Qt.UserRole)
        if ep:
            player = VLCPlayerWindow(config, self.theme)
            player.play(ep.path)
            player.show()

        
    def update_metadata_panel(self):
        if self.series_item.poster_path:
            pixmap = QtGui.QPixmap(self.series_item.poster_path)
            self.poster_label.setPixmap(pixmap)
        self.overview_label.setHtml(f"<b>{self.series_item.title}</b><br><br>{self.series_item.overview}")

    def play_selected_episode(self):
        selected = self.list_view.selectedItems()
        if not selected:
            return
        item = selected[0]
        if item.childCount() == 0:
            self.play_episode(item.text(1))

    def play_episode(self, path):
        if not os.path.exists(path):
            QtWidgets.QMessageBox.warning(self, "File not found", path)
            return

        player = VLCPlayerWindow(config, self.theme)
        self.player_windows.append(player)
        # Stop playback and release when window closes
        player.closed.connect(lambda: self.player_windows.remove(player) if player in self.player_windows else None)
        player.show()
        player.play(path)



class VideoModel(QtCore.QAbstractListModel):
    def __init__(self, videos):
        super().__init__()
        self._videos = videos

    def rowCount(self, parent=QtCore.QModelIndex()):
        return len(self._videos)

    def data(self, index, role):
        if not index.isValid(): return None
        video = self._videos[index.row()]
        if role == QtCore.Qt.DisplayRole:
            return video.title
        elif role == QtCore.Qt.UserRole:
            return video
        return None

    def updateVideos(self, videos):
        self.beginResetModel()
        self._videos = videos
        self.endResetModel()

# ---------------- Video Delegate ----------------
class VideoDelegate(QtWidgets.QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread_pool = QtCore.QThreadPool.globalInstance()
        self.poster_width = 150
        self.poster_height = 180
        self.title_height = 50  # space for 2–3 lines of text

    def paint(self, painter, option, index):
        video = index.data(QtCore.Qt.UserRole)
        painter.save()

        # Draw background
        if option.state & QtWidgets.QStyle.State_Selected:
            painter.fillRect(option.rect, QtGui.QColor("#444"))
        else:
            painter.fillRect(option.rect, QtGui.QColor("#222"))

        # Draw poster
        poster_rect = QtCore.QRect(option.rect.x() + 5, option.rect.y() + 5,
                                   self.poster_width, self.poster_height)
        if video.pixmap:
            painter.drawPixmap(poster_rect, video.pixmap)
        else:
            painter.fillRect(poster_rect, QtGui.QColor("#555"))
            if video.poster_path:
                self.load_poster(video, self.poster_width, self.poster_height, index)

        # Draw title (wrapped)
        text_rect = QtCore.QRect(option.rect.x() + 5,
                                 option.rect.y() + self.poster_height + 10,
                                 self.poster_width, self.title_height)
        painter.setPen(QtGui.QColor("#eee"))
        painter.drawText(text_rect, QtCore.Qt.TextWordWrap | QtCore.Qt.AlignTop, video.title)

        painter.restore()

    def sizeHint(self, option, index):
        # Total item height = poster + spacing + title
        total_height = self.poster_height + 10 + self.title_height + 5
        return QtCore.QSize(self.poster_width + 10, total_height)

    def load_poster(self, video, w, h, index):
        if video.poster_path:
            pixmap = QtGui.QPixmap(video.poster_path)
            pixmap = pixmap.scaled(w, h, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            video.pixmap = pixmap
            QtCore.QMetaObject.invokeMethod(
                self.parent(), "update", QtCore.Qt.QueuedConnection
            )


# ---------------- Main Library Window ----------------
class LibraryWindow(QtWidgets.QWidget):
    THEMES = {
        "Dark": {"window": "#222", "text": "#eee", "panel": "#333", "combo_bg": "#222", "combo_text": "#eee"},
        "Light": {"window": "#f0f0f0", "text": "#000", "panel": "#ddd", "combo_bg": "#fff", "combo_text": "#000"},
        "Netflix": {"window": "#141414", "text": "#e50914", "panel": "#222", "combo_bg": "#222", "combo_text": "#e50914"},
        "Fun": {"window": "#fffae3", "text": "#222", "panel": "#ffd700", "combo_bg": "#ffd700", "combo_text": "#222"}
    }

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.folders = self.config.get(
            "folders", 
            [self.config.get("last_dir", os.path.expanduser("~"))]
        )
        self.current_theme = self.config.get("theme", "Dark")
        self.player_windows = []

        self.setMouseTracking(True)           # Main window

        self.zoom_factor = 1.0  # 1x zoom
        self.video_items = []

        # Main layout
        main_layout = QtWidgets.QHBoxLayout(self)

        # --- Left panel ---
        left_widget = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_widget)

        # Top row: search, add, refresh, zoom, genre
        top_row = QtWidgets.QHBoxLayout()
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("Search videos or series...")
        self.search_input.textChanged.connect(self.on_search_changed)
        top_row.addWidget(self.search_input, 2)

        add_folder_btn = QtWidgets.QPushButton("Add Folder(s)")
        add_folder_btn.clicked.connect(self.add_folders)
        top_row.addWidget(add_folder_btn)

        refresh_btn = QtWidgets.QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_list)
        top_row.addWidget(refresh_btn)

        zoom_in_btn = QtWidgets.QPushButton("Zoom +")
        zoom_in_btn.clicked.connect(lambda: self.change_zoom(1.2))
        top_row.addWidget(zoom_in_btn)

        zoom_out_btn = QtWidgets.QPushButton("Zoom -")
        zoom_out_btn.clicked.connect(lambda: self.change_zoom(0.8))
        top_row.addWidget(zoom_out_btn)
        
        # Theme combo
        self.theme_combo = QtWidgets.QComboBox()
        self.theme_combo.addItems(self.THEMES.keys())

        # Restore last theme from config
        last_theme = self.config.get("theme", "Dark")
        if last_theme in self.THEMES:
            self.theme_combo.setCurrentText(last_theme)

        self.theme_combo.currentTextChanged.connect(self.on_theme_changed)
        top_row.addWidget(self.theme_combo)
        print("Themes restored")


        # Type filter combo
        self.type_combo = QtWidgets.QComboBox()
        self.type_combo.addItems(["All", "Movies", "Series"])
        print("Movie Series Filter Added")

        # Load last type filter from config
        last_type = str(self.config.get("last_type", "All")).strip()
        index = self.type_combo.findText(last_type)
        if index != -1:
            self.type_combo.setCurrentIndex(index)
            self.type_combo.currentTextChanged.connect(self.on_type_changed)
        
        else:
            self.type_combo.currentTextChanged.connect(self.refresh_list)
        top_row.addWidget(self.type_combo)
        print("** Connected")

        # Include Subfolders Checkbox (Currently used for Series)
        self.recursive_checkbox = QtWidgets.QCheckBox("Include subfolders")
        self.recursive_checkbox.setChecked(False)  # default OFF
        top_row.addWidget(self.recursive_checkbox)
        
        # Genre combo
        self.genre_combo = QtWidgets.QComboBox()
        self.genre_combo.currentTextChanged.connect(self.refresh_list)
        top_row.addWidget(self.genre_combo)

        left_layout.addLayout(top_row)

        # QListView
        self.list_view = QtWidgets.QListView()
        self.list_view.setViewMode(QtWidgets.QListView.IconMode)
        self.list_view.setResizeMode(QtWidgets.QListView.Adjust)
        self.list_view.setSpacing(10)
        self.delegate = VideoDelegate(self.list_view)
        self.list_view.setItemDelegate(self.delegate)
        self.list_view.doubleClicked.connect(self.on_double_click)
        left_layout.addWidget(self.list_view)
        self.list_view.clicked.connect(self.on_item_clicked)

        self.list_view.setDragEnabled(False)
        self.list_view.setAcceptDrops(False)
        self.list_view.setDropIndicatorShown(False)

        main_layout.addWidget(left_widget, 3)

        # Right panel: description
        self.desc_panel = QtWidgets.QTextEdit()
        self.desc_panel.setReadOnly(True)
        main_layout.addWidget(self.desc_panel, 1)

        # Search debounce
        self.search_timer = QtCore.QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.refresh_list)

        # Load TMDb genres
        self.genres = get_tmdb_genres()            # {id: name}
        self.genre_name_to_id = {v: k for k, v in self.genres.items()}

        # Apply theme
        self.apply_theme(self.current_theme)

        # Window settings
        self.setWindowTitle("My Video Library")
        self.showMaximized()
        #self.showFullScreen()

        # Initial refresh
        self.refresh_list()
        
    def on_theme_changed(self, theme_name):
        self.config["theme"] = theme_name
        save_config(self.config)
        self.apply_theme(theme_name)


    def on_type_changed(self, text):
        self.config["last_type"] = text
        save_config(self.config)
        self.refresh_list()



    def _rebuild_model(self, filtered_items=None):
        """Build or update the QStandardItemModel from given VideoItems."""
        if filtered_items is None:
            filtered_items = self.video_items

        model = QtGui.QStandardItemModel()
        for vi in filtered_items:
            item = QtGui.QStandardItem(vi.title)
            item.setData(vi, QtCore.Qt.UserRole)

            # 🔑 Always re-load poster from disk if available
            if vi.poster_path and os.path.exists(vi.poster_path):
                pixmap = QtGui.QPixmap(vi.poster_path)
                if not pixmap.isNull():
                    icon = QtGui.QIcon(
                        pixmap.scaled(
                            self.delegate.poster_width,
                            self.delegate.poster_height,
                            QtCore.Qt.KeepAspectRatio,
                            QtCore.Qt.SmoothTransformation,
                        )
                    )
                    item.setIcon(icon)

            model.appendRow(item)

        self.list_view.setModel(model)
        
    def on_genre_changed(self, genre_name):
        if genre_name == "All Genres":
            self._rebuild_model(self.video_items)
            return

        # Map genre name back to IDs
        genre_id = None
        for gid, name in self.genres.items():
            if name == genre_name:
                genre_id = gid
                break

        if genre_id is None:
            self._rebuild_model(self.video_items)
            return

        # Filter items
        filtered = [vi for vi in self.video_items if genre_id in getattr(vi, "genres", [])]
        self._rebuild_model(filtered)

    # ---------------- Zoom ----------------
    def change_zoom(self, factor):
        self.zoom_factor *= factor
        self.zoom_factor = max(0.5, min(3.0, self.zoom_factor))
        self.delegate.poster_width = int(150 * self.zoom_factor)
        self.delegate.poster_height = int(180 * self.zoom_factor)
        self.delegate.title_height = int(50 * self.zoom_factor)
        self.list_view.reset()

    def wheelEvent(self, event):
        if event.modifiers() & QtCore.Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.change_zoom(1.1)
            else:
                self.change_zoom(0.9)
            event.accept()
        else:
            super().wheelEvent(event)

    # ---------------- Search ----------------
    def on_search_changed(self):
        self.search_timer.start(250)

    # ---------------- Folder management ----------------
    def add_folders(self):
        paths = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose folder")
        if paths:
            if isinstance(paths, str):
                paths = [paths]
            for p in paths:
                if p not in self.folders:
                    self.folders.append(p)

            # Save to config immediately
            self.config["folders"] = self.folders
            save_config(self.config)

            self.refresh_list()

    # ---------------- Refresh & filter list ----------------
    def refresh_list(self):
        items = []

        # --- Scan folders ---
        for folder in self.folders:
            if not os.path.exists(folder):
                continue

            for entry in os.scandir(folder):
                if entry.is_file() and entry.name.lower().endswith(VIDEO_EXTS):
                    vi = VideoItem(entry.path)
                    items.append(vi)
                elif entry.is_dir():
                    has_videos = any(
                        f.is_file() and f.name.lower().endswith(VIDEO_EXTS)
                        for f in os.scandir(entry.path)
                    )
                    if has_videos:
                        si = SeriesItem(entry.path)
                        items.append(si)


        # --- Apply search filter ---
        filter_text = self.search_input.text().lower()
        items = [it for it in items if filter_text in it.title.lower()]
        self.video_items = items

        # --- Build dynamic genre dropdown ---
        genre_ids_in_items = set()
        for it in items:
            genre_ids_in_items.update(getattr(it, "genres", []))

        available_genres = {gid: self.genres.get(gid, "Unknown") for gid in genre_ids_in_items}
        self.genre_combo.blockSignals(True)
        current_selection = self.genre_combo.currentText()
        self.genre_combo.clear()
        self.genre_combo.addItem("All Genres")
        for name in sorted(available_genres.values()):
            self.genre_combo.addItem(name)
        idx = self.genre_combo.findText(current_selection)
        if idx >= 0:
            self.genre_combo.setCurrentIndex(idx)
        self.genre_combo.blockSignals(False)

        # --- Apply genre filter ---
        selected_genre = self.genre_combo.currentText()
        if selected_genre != "All Genres":
            genre_ids = [gid for gid, name in self.genres.items() if name == selected_genre]
            if genre_ids:
                self.video_items = [
                    it for it in self.video_items
                    if any(gid in getattr(it, "genres", []) for gid in genre_ids)
                ]

        # --- Build model ---
        model = QtGui.QStandardItemModel()
        for it in self.video_items:
            item = QtGui.QStandardItem(it.title)
            item.setData(it, QtCore.Qt.UserRole)

            # Icon logic
            if isinstance(it, SeriesItem):
                if it.poster_path:
                    pixmap = QtGui.QPixmap(it.poster_path)
                    icon = QtGui.QIcon(pixmap)
                else:
                    icon = QtGui.QIcon(":/icons/folder.png")
            elif it.poster_path:
                pixmap = QtGui.QPixmap(it.poster_path)
                icon = QtGui.QIcon(pixmap)
            else:
                icon = QtGui.QIcon()

            item.setIcon(icon)
            model.appendRow(item)

            # Connect updated signal for movies and series
            if hasattr(it, "updated"):
                it.updated.connect(lambda v=it, it_row=item: self.update_list_item(it_row, v))

        self.list_view.setModel(model)




    # ---------------- Update UI ----------------
    def update_list_item(self, item, obj):
        """Update a list item when poster/metadata is fetched."""
        if obj.poster_path and os.path.exists(obj.poster_path):
            pixmap = QtGui.QPixmap(obj.poster_path)
            item.setIcon(QtGui.QIcon(pixmap))
        elif isinstance(obj, SeriesItem):
            item.setIcon(QtGui.QIcon(":/icons/folder.png"))
        else:
            item.setIcon(QtGui.QIcon(":/icons/video.png"))

        item.setText(obj.title)


    def update_desc_panel(self, video_item):
        self.desc_panel.setHtml(f'<span style="font-size:16pt;"><b>{video_item.title}</b><br><br>{video_item.overview}')

    # ---------------- Clicks ----------------
    def on_double_click(self, index):
        item = index.data(QtCore.Qt.UserRole)
        if isinstance(item, SeriesItem):
            # Keep a reference so it doesn’t get garbage collected
            if not hasattr(self, "series_windows"):
                self.series_windows = []
            viewer = SeriesViewerWindow(item, self.current_theme)
            self.series_windows.append(viewer)
            # Remove from list when closed
            viewer.destroyed.connect(lambda: self.series_windows.remove(viewer) if viewer in self.series_windows else None)
            viewer.show()
        elif isinstance(item, VideoItem):
            self.launch_player(item.path)



    def on_item_clicked(self, index):
        video = index.data(QtCore.Qt.UserRole)
        if video:
            self.update_desc_panel(video)

    # ---------------- Launch VLC ----------------
    def launch_player(self, path):
        player = VLCPlayerWindow(self.config)
        self.player_windows.append(player)
        player.destroyed.connect(lambda _: self.player_windows.remove(player) if player in self.player_windows else None)
        player.play(path)
        self.config["last_watched"] = path
        save_config(self.config)
        player.show()

    # ---------------- Theme ----------------
    """
    def apply_theme(self, name):
        t = self.THEMES.get(name, self.THEMES["Dark"])
        pal = self.palette()
        pal.setColor(QtGui.QPalette.Window, QtGui.QColor(t["window"]))
        pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor(t["text"]))
        self.setPalette(pal)
        self.desc_panel.setStyleSheet(f"background:{t['panel']}; color:{t['text']}; font-size:14px;")"""
    def apply_theme(self, theme_name):
        self.current_theme = theme_name
        theme = self.THEMES[theme_name]

        # Apply main window style
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {theme['window']};
                color: {theme['text']};
            }}
        """)

        # Apply styles to combo boxes (all QComboBox children)
        for combo in self.findChildren(QtWidgets.QComboBox):
            combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: {theme['combo_bg']};
                    color: {theme['combo_text']};
                    padding: 2px 5px;
                }}
                QComboBox::drop-down {{
                    subcontrol-origin: padding;
                    subcontrol-position: top right;
                    width: 15px;
                    border-left-width: 1px;
                    border-left-color: gray;
                    border-left-style: solid;
                    border-radius: 0px;
                }}
                QComboBox QAbstractItemView {{
                    background-color: {theme['combo_bg']};
                    color: {theme['combo_text']};
                    selection-background-color: {theme['panel']};
                }}
            """)



if __name__=="__main__":
    app=QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    palette=QtGui.QPalette(); palette.setColor(QtGui.QPalette.Window,QtGui.QColor("#222"))
    palette.setColor(QtGui.QPalette.WindowText,QtGui.QColor("#eee"))
    app.setPalette(palette)
    library_window=LibraryWindow(config)
    library_window.show()
    sys.exit(app.exec_())
