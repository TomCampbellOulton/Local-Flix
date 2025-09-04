import sys, os, re, json, platform, requests, vlc, threading
from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtWidgets import QGraphicsOpacityEffect
from PyQt5.QtCore import QPropertyAnimation, QEasingCurve
from tmdbv3api import TMDb, Movie
from datetime import timedelta
import functools

CONFIG_FILE = "config.json"
VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm")

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

def list_videos_in_dirs(paths):
    result=[]
    for path in paths:
        try:
            for entry in os.scandir(path):
                if entry.is_file() and entry.name.lower().endswith(VIDEO_EXTS):
                    result.append(entry.path)
                elif entry.is_dir():
                    try:
                        for sub in os.scandir(entry.path):
                            if sub.is_file() and sub.name.lower().endswith(VIDEO_EXTS):
                                result.append(sub.path)
                    except: continue
        except: continue
    return sorted(result)

def get_tmdb_genres():
    url = f"https://api.themoviedb.org/3/genre/movie/list?api_key={TMDB_API_KEY}&language=en-US"
    try:
        response = requests.get(url, timeout=5).json()
        # returns {id: name}
        return {g["id"]: g["name"] for g in response.get("genres", [])}
    except:
        return {}

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

import os, platform, sys
from PyQt5 import QtCore, QtGui, QtWidgets
import vlc

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

    def __init__(self, config):
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
        self.controls.setStyleSheet("background-color: red;")

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

    def toggle_mute(self):
        self.mplayer.audio_toggle_mute()

    # --- Fade controls ---
    def fade_in_controls(self):
        print("Fading in")
        self.anim.stop()
        self.anim.setDuration(200)
        self.anim.setStartValue(self.opacity_effect.opacity())
        self.anim.setEndValue(1.0)
        self.anim.start()
        self.inactivity_timer.start()  # restart timer

    def fade_out_controls(self):
        print("Fading out")
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
        print("Movement?")
        self.fade_in_controls()
        super().mouseMoveEvent(event)

    # --- Event filter for global mouse detection ---
    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.MouseMove:
            print("Movement Detected")
            self.fade_in_controls()
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
    """def load_tracks(self):
        #Populate subtitle and audio track dropdowns safely.
        print("Done")
        try:
            # Subtitles
            self.subtitle_dropdown.clear()
            print("Done")
            spu_tracks = self.mplayer.video_get_spu_description()
            if spu_tracks:
                for track in spu_tracks:
                    try:
                        tid, name = track
                        self.subtitle_dropdown.addItem(str(name), tid)
                    except Exception as inner_e:
                        print("Bad SPU track entry:", track, inner_e)

                print("Done")
                current_spu = self.mplayer.video_get_spu()
                idx = self.subtitle_dropdown.findData(current_spu)
                if idx >= 0:
                    self.subtitle_dropdown.setCurrentIndex(idx)

            # Audio
            print("Done")
            self.audio_dropdown.clear()
            audio_tracks = self.mplayer.audio_get_track_description()
            print("Done")
            if audio_tracks:
                for track in audio_tracks:
                    try:
                        tid, name = track
                        self.audio_dropdown.addItem(str(name), tid)
                    except Exception as inner_e:
                        print("Bad audio track entry:", track, inner_e)

                current_audio = self.mplayer.audio_get_track()
                idx = self.audio_dropdown.findData(current_audio)
                if idx >= 0:
                    self.audio_dropdown.setCurrentIndex(idx)
                    print("Done")

        except Exception as e:
            print("load_tracks error:", e)"""
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
        "Dark": {"window": "#222", "text": "#eee", "panel": "#333"},
        "Light": {"window": "#f0f0f0", "text": "#000", "panel": "#ddd"},
        "Netflix": {"window": "#141414", "text": "#e50914", "panel": "#222"},
        "Fun": {"window": "#fffae3", "text": "#222", "panel": "#ffd700"}
    }

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.folders = self.config.get("folders", [self.config.get("last_dir", os.path.expanduser("~"))])
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
        self.resize(1600, 900)
        self.setWindowTitle("My Video Library")
        self.show()

        # Initial refresh
        self.refresh_list()

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
            self.refresh_list()

    # ---------------- Refresh & filter list ----------------
    def refresh_list(self):
        # 1. List videos
        videos_paths = list_videos_in_dirs(self.folders)
        filter_text = self.search_input.text().lower()
        videos_paths = [v for v in videos_paths if filter_text in os.path.basename(v).lower()]

        # 2. Convert to VideoItem objects
        self.video_items = [VideoItem(v) for v in videos_paths]

        # 3. Dynamic genre dropdown based on available videos
        genre_ids_in_videos = set()
        for vi in self.video_items:
            genre_ids_in_videos.update(getattr(vi, "genres", []))

        available_genres = {gid: self.genres.get(gid, "Unknown") for gid in genre_ids_in_videos}

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

        # 4. Filter by selected genre
        selected_genre = self.genre_combo.currentText()
        if selected_genre != "All Genres":
            genre_ids = [gid for gid, name in self.genres.items() if name == selected_genre]
            if genre_ids:
                self.video_items = [
                    vi for vi in self.video_items
                    if any(gid in getattr(vi, "genres", []) for gid in genre_ids)
                ]

        # 5. Build the list model
        if not self.video_items:
            self.list_view.setModel(QtGui.QStandardItemModel())
            return

        model = QtGui.QStandardItemModel()
        for vi in self.video_items:
            item = QtGui.QStandardItem(vi.title)
            item.setData(vi, QtCore.Qt.ItemDataRole.UserRole)

            if vi.poster_path:
                pixmap = QtGui.QPixmap(vi.poster_path)
                icon = QtGui.QIcon(pixmap)
                item.setIcon(icon)

            model.appendRow(item)

            # Update icon when poster is downloaded
            vi.updated.connect(lambda v=vi, it=item: self.update_list_item(it, v))

        self.list_view.setModel(model)

    # ---------------- Update UI ----------------
    def update_list_item(self, list_item, video_item):
        if video_item.poster_path:
            pixmap = QtGui.QPixmap(video_item.poster_path)
            icon = QtGui.QIcon(pixmap)
            list_item.setIcon(icon)

    def update_desc_panel(self, video_item):
        self.desc_panel.setHtml(f"<b>{video_item.title}</b><br><br>{video_item.overview}")

    # ---------------- Clicks ----------------
    def on_double_click(self, index):
        video = index.data(QtCore.Qt.UserRole)
        self.launch_player(video.path)

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
    def apply_theme(self, name):
        t = self.THEMES.get(name, self.THEMES["Dark"])
        pal = self.palette()
        pal.setColor(QtGui.QPalette.Window, QtGui.QColor(t["window"]))
        pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor(t["text"]))
        self.setPalette(pal)
        self.desc_panel.setStyleSheet(f"background:{t['panel']}; color:{t['text']}; font-size:14px;")


if __name__=="__main__":
    app=QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    palette=QtGui.QPalette(); palette.setColor(QtGui.QPalette.Window,QtGui.QColor("#222"))
    palette.setColor(QtGui.QPalette.WindowText,QtGui.QColor("#eee"))
    app.setPalette(palette)
    library_window=LibraryWindow(config)
    library_window.show()
    sys.exit(app.exec_())
