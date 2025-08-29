import sys, os, re, json, platform, requests, vlc
from PyQt5 import QtWidgets, QtCore, QtGui
from tmdbv3api import TMDb, Movie
from datetime import timedelta

CONFIG_FILE = "config.json"
VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm")

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE,"r") as f:
                return json.load(f)
        except: return {}
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

def format_seconds(seconds):
    return str(timedelta(seconds=int(seconds)))

class VLCPlayerWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__(None)
        self.setWindowTitle("VLC Player")
        self.resize(1280,720)
        self.instance = vlc.Instance()
        self.mplayer = self.instance.media_player_new()
        self.video_frame = QtWidgets.QFrame(self)
        self.video_frame.setStyleSheet("background:black;")
        self.setCentralWidget(self.video_frame)

        self.controls = QtWidgets.QWidget(self)
        self.hbox = QtWidgets.QHBoxLayout(self.controls)
        self.hbox.setContentsMargins(5,5,5,5)
        self.play_btn = QtWidgets.QPushButton("⏸ Pause")
        self.play_btn.clicked.connect(self.toggle_play)
        self.position_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.position_slider.setRange(0,1000)
        self.position_slider.sliderMoved.connect(self.set_position)
        self.time_label = QtWidgets.QLabel("00:00 / 00:00")
        self.volume_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.volume_slider.setRange(0,100)
        self.volume_slider.setValue(50)
        self.volume_slider.valueChanged.connect(lambda val: self.mplayer.audio_set_volume(val))
        self.hbox.addWidget(self.play_btn)
        self.hbox.addWidget(self.position_slider,1)
        self.hbox.addWidget(self.time_label)
        self.hbox.addWidget(QtWidgets.QLabel("Vol"))
        self.hbox.addWidget(self.volume_slider)
        dock = QtWidgets.QDockWidget("Controls", self)
        dock.setFeatures(QtWidgets.QDockWidget.NoDockWidgetFeatures)
        dock.setTitleBarWidget(QtWidgets.QWidget())
        dock.setWidget(self.controls)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, dock)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.update_slider)
        self.is_fullscreen=False
        self._current_media_path=None

        # Auto-hide controls
        self.setMouseTracking(True)
        self.video_frame.setMouseTracking(True)
        self.inactivity_timer = QtCore.QTimer(self)
        self.inactivity_timer.setInterval(5000)
        self.inactivity_timer.timeout.connect(lambda: self.controls.setVisible(False))
        self.inactivity_timer.start()

        self.position_slider.mousePressEvent = self.slider_click_seek

    def slider_click_seek(self, event):
        if self.mplayer:
            ratio = event.x()/self.position_slider.width()
            ratio = max(0,min(ratio,1))
            self.mplayer.set_position(ratio)
            self.position_slider.setValue(int(ratio*1000))

    def mouseMoveEvent(self,event):
        self.controls.setVisible(True)
        self.inactivity_timer.start()
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self,event):
        self.toggle_fullscreen()
        event.accept()

    def keyPressEvent(self,event):
        if event.key() in (QtCore.Qt.Key_F11, QtCore.Qt.Key_F):
            self.toggle_fullscreen()
        elif event.key() == QtCore.Qt.Key_Escape:
            if self.is_fullscreen: self.toggle_fullscreen()
            else: self.close()
        elif event.key() == QtCore.Qt.Key_Space: self.toggle_play()
        else: super().keyPressEvent(event)

    def toggle_fullscreen(self):
        if not self.is_fullscreen:
            self.showFullScreen()
            self.setWindowFlags(QtCore.Qt.FramelessWindowHint)
            self.show()
            self.is_fullscreen = True
        else:
            self.setWindowFlags(QtCore.Qt.Window)
            self.showNormal()
            self.is_fullscreen = False

    def play(self,path:str):
        if not os.path.exists(path):
            QtWidgets.QMessageBox.warning(self,"File not found",f"Cannot find: {path}"); return
        media=self.instance.media_new(path); self.mplayer.set_media(media)
        winid=int(self.video_frame.winId()); sysplat=platform.system()
        if sysplat=="Windows": self.mplayer.set_hwnd(winid)
        elif sysplat=="Darwin": 
            try: self.mplayer.set_nsobject(winid)
            except: pass
        else: self.mplayer.set_xwindow(winid)
        subtitle_path=os.path.splitext(path)[0]+".srt"
        if os.path.exists(subtitle_path):
            try: self.mplayer.video_set_subtitle_file(subtitle_path)
            except: pass
        self.mplayer.play(); self.timer.start(); self._current_media_path=path
        self.showFullScreen(); self.is_fullscreen=True

    def toggle_play(self):
        if not self.mplayer: return
        if self.mplayer.is_playing(): self.mplayer.pause(); self.play_btn.setText("▶ Play")
        else: self.mplayer.play(); self.play_btn.setText("⏸ Pause")

    def set_position(self,pos):
        if self.mplayer:
            try: self.mplayer.set_position(pos/1000.0)
            except: pass

    def update_slider(self):
        if self.mplayer:
            try:
                pos=int(self.mplayer.get_position()*1000)
                if 0<=pos<=1000: self.position_slider.setValue(pos)
                length = self.mplayer.get_length()/1000 if self.mplayer.get_length() >0 else 0
                current = self.mplayer.get_time()/1000 if self.mplayer.get_time() >0 else 0
                self.time_label.setText(f"{format_seconds(current)} / {format_seconds(length)}")
            except: pass

    def stop_and_release(self):
        try:
            if self.mplayer: self.mplayer.stop(); 
            try: self.mplayer.release()
            except: pass; self.mplayer=None
        except: pass
        try: self.timer.stop()
        except: pass
        try:
            if hasattr(self,"instance") and self.instance: 
                try:self.instance.release()
                except: pass; self.instance=None
        except: pass

    def closeEvent(self,event): self.stop_and_release(); event.accept()

class LibraryWindow(QtWidgets.QWidget):
    THEMES = {
        "Dark": {"window":"#222", "text":"#eee", "panel":"#333"},
        "Light": {"window":"#f0f0f0", "text":"#000", "panel":"#ddd"},
        "Netflix": {"window":"#141414", "text":"#e50914", "panel":"#222"},
        "Fun": {"window":"#fffae3", "text":"#222", "panel":"#ffd700"}
    }

    def __init__(self):
        super().__init__(None)
        self.setWindowTitle("My Video Library")
        self.resize(1400,900)
        self.config=config
        self.folders=self.config.get("folders",[self.config.get("last_dir",os.path.expanduser("~"))])
        self.last_watched=self.config.get("last_watched","")
        self.current_theme=self.config.get("theme","Dark")
        main_layout=QtWidgets.QHBoxLayout(self)
        left_widget=QtWidgets.QWidget()
        self.left_layout=QtWidgets.QVBoxLayout(left_widget)
        top_row=QtWidgets.QHBoxLayout()
        self.search_input=QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("Search videos or series...")
        self.search_input.textChanged.connect(self.refresh_list)
        top_row.addWidget(self.search_input,2)
        add_folder_btn=QtWidgets.QPushButton("Add Folder(s)")
        add_folder_btn.clicked.connect(self.add_folders)
        top_row.addWidget(add_folder_btn)
        refresh_btn=QtWidgets.QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_list)
        top_row.addWidget(refresh_btn)
        self.theme_combo=QtWidgets.QComboBox()
        self.theme_combo.addItems(self.THEMES.keys())
        self.theme_combo.setCurrentText(self.current_theme)
        self.theme_combo.currentTextChanged.connect(self.change_theme)
        top_row.addWidget(self.theme_combo)
        self.left_layout.addLayout(top_row)
        self.scroll_area=QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_widget=QtWidgets.QWidget()
        self.grid_layout=QtWidgets.QGridLayout(self.scroll_widget)
        self.scroll_area.setWidget(self.scroll_widget)
        self.left_layout.addWidget(self.scroll_area)
        main_layout.addWidget(left_widget,3)
        self.desc_panel=QtWidgets.QTextEdit()
        self.desc_panel.setReadOnly(True)
        main_layout.addWidget(self.desc_panel,1)
        self.player_windows=[]
        self.apply_theme(self.current_theme)
        self.refresh_list()

    def change_theme(self,name):
        self.current_theme=name
        self.apply_theme(name)
        self.config["theme"]=name; save_config(self.config)

    def apply_theme(self,name):
        t=self.THEMES.get(name,self.THEMES["Dark"])
        pal=self.palette()
        pal.setColor(QtGui.QPalette.Window,QtGui.QColor(t["window"]))
        pal.setColor(QtGui.QPalette.WindowText,QtGui.QColor(t["text"]))
        self.setPalette(pal)
        self.desc_panel.setStyleSheet(f"background:{t['panel']}; color:{t['text']}; font-size:14px;")

    def add_folders(self):
        paths=QtWidgets.QFileDialog.getExistingDirectory(self,"Choose folder")
        if paths:
            if isinstance(paths,str): paths=[paths]
            for p in paths:
                if p not in self.folders: self.folders.append(p)
            self.config["folders"]=self.folders
            save_config(self.config)
            self.refresh_list()

    def resizeEvent(self,event):
        self.refresh_list()
        super().resizeEvent(event)

    def refresh_list(self):
        for i in reversed(range(self.grid_layout.count())):
            w = self.grid_layout.itemAt(i).widget()
            if w: w.setParent(None)

        videos = list_videos_in_dirs(self.folders)
        filter_text = self.search_input.text().lower()
        videos = [v for v in videos if filter_text in os.path.basename(v).lower()]
        if not videos: return

        available_width = self.scroll_area.viewport().width()
        max_cols = max(1, available_width // 270)
        poster_width = available_width // max_cols - 20
        poster_height = int(poster_width * 1.2)  # reduced ratio

        row, col = 0, 0
        for video_path in videos:
            filename = os.path.basename(video_path)
            movie = search_movie_by_filename(filename)
            poster_pixmap = QtGui.QPixmap(poster_width, poster_height)
            poster_pixmap.fill(QtGui.QColor("#444"))
            tooltip_text = "No description available."
            title_text = filename
            if movie:
                title_text = movie.title
                tooltip_text = movie.overview if movie.overview else tooltip_text
                if movie.poster_path:
                    poster_url = f"https://image.tmdb.org/t/p/w300{movie.poster_path}"
                    try:
                        data = requests.get(poster_url, timeout=5).content
                        poster_pixmap.loadFromData(data)
                        poster_pixmap = poster_pixmap.scaled(
                            poster_width, poster_height,
                            QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
                        )
                    except: pass

            thumb_widget = QtWidgets.QWidget()
            vbox = QtWidgets.QVBoxLayout(thumb_widget)
            label_img = QtWidgets.QLabel()
            label_img.setPixmap(poster_pixmap)
            label_img.setAlignment(QtCore.Qt.AlignCenter)
            label_img.setFixedSize(poster_width, poster_height)
            vbox.addWidget(label_img)
            title_label = QtWidgets.QLabel(title_text)
            title_label.setAlignment(QtCore.Qt.AlignCenter)
            title_label.setWordWrap(True)
            title_label.setMaximumHeight(40)
            vbox.addWidget(title_label)
            thumb_widget.setToolTip(tooltip_text)
            label_img.setToolTip(tooltip_text)
            title_label.setToolTip(tooltip_text)
            thumb_widget.mouseDoubleClickEvent = lambda e, p=video_path: self.launch_player(p)
            thumb_widget.mousePressEvent = lambda e, p=video_path, m=movie: self.update_desc_panel(p, m)
            self.grid_layout.addWidget(thumb_widget, row, col)
            col += 1
            if col >= max_cols:
                col = 0
                row += 1

    def update_desc_panel(self, path, movie):
        if movie:
            self.desc_panel.setHtml(f"<b>{movie.title}</b><br><br>{movie.overview}")
        else:
            self.desc_panel.setText(os.path.basename(path))

    def launch_player(self,path):
        player=VLCPlayerWindow()
        self.player_windows.append(player)
        player.destroyed.connect(lambda _: self.player_windows.remove(player) if player in self.player_windows else None)
        player.play(path)
        self.config["last_watched"]=path; save_config(self.config)
        player.show()

if __name__=="__main__":
    app=QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    palette=QtGui.QPalette(); palette.setColor(QtGui.QPalette.Window,QtGui.QColor("#222"))
    palette.setColor(QtGui.QPalette.WindowText,QtGui.QColor("#eee"))
    app.setPalette(palette)
    library_window=LibraryWindow()
    library_window.show()
    sys.exit(app.exec_())
