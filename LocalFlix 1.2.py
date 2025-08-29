from __future__ import annotations

import json
import os
import platform
import re
import sys
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict, List, Tuple

import requests
from PySide6 import QtCore, QtGui, QtWidgets

try:
    from guessit import guessit
except Exception:
    guessit = None

try:
    import vlc
except Exception:
    vlc = None

APP_NAME = "LocalFlix"
SUPPORTED_EXTS = {".mkv", ".mp4", ".avi"}
TMDB_API_URL = "https://api.themoviedb.org/3"
TMDB_IMG_BASE = "https://image.tmdb.org/t/p"

@dataclass
class MovieMeta:
    path: str
    title: str
    year: Optional[int] = None
    tmdb_id: Optional[int] = None
    overview: Optional[str] = None
    poster_path: Optional[str] = None

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "MovieMeta":
        return cls(**d)
"""
class VLCPlayerWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.instance = vlc.Instance()
        self.mplayer = self.instance.media_player_new()

        # Main layout
        self.vbox = QtWidgets.QVBoxLayout(self)
        self.vbox.setContentsMargins(0,0,0,0)
        self.vbox.setSpacing(0)

        # Video frame (fills space)
        self.video_frame = QtWidgets.QFrame(self)
        self.video_frame.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.vbox.addWidget(self.video_frame)

        # Controls at bottom
        self.controls = QtWidgets.QWidget(self)
        self.hbox = QtWidgets.QHBoxLayout(self.controls)
        self.hbox.setContentsMargins(5,5,5,5)
        self.play_btn = QtWidgets.QPushButton("⏸ Pause")
        self.play_btn.clicked.connect(self.toggle_play)
        self.position_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.position_slider.setRange(0, 1000)
        self.position_slider.sliderMoved.connect(self.set_position)
        self.hbox.addWidget(self.play_btn)
        self.hbox.addWidget(self.position_slider)
        self.vbox.addWidget(self.controls)

        # Timer to update slider
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.update_slider)"""

class VLCPlayerWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.instance = vlc.Instance()
        self.mediaplayer = self.instance.media_player_new()

        # Video frame
        self.videoframe = QtWidgets.QFrame()
        self.videoframe.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding
        )
        self.videoframe.setStyleSheet("background: black;")

        # Controls
        self.playbutton = QtWidgets.QPushButton("Pause")
        self.playbutton.clicked.connect(self.toggle_play)

        self.positionslider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.positionslider.setRange(0, 1000)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(self.playbutton)
        controls.addWidget(self.positionslider)

        # Layout
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.videoframe)   # <-- VIDEO FIRST
        layout.addLayout(controls)          # <-- CONTROLS BELOW

        # Tell VLC where to render video
        if sys.platform.startswith("linux"):
            self.mediaplayer.set_xwindow(self.videoframe.winId())
        elif sys.platform == "win32":
            self.mediaplayer.set_hwnd(self.videoframe.winId())
        elif sys.platform == "darwin":
            self.mediaplayer.set_nsobject(int(self.videoframe.winId()))


    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            self.stop()
            self.parentWidget().close()  # closes the player window


    def play(self, path: str):
        media = self.instance.media_new(path)
        self.mplayer.set_media(media)
        winid = int(self.video_frame.winId())
        sysplat = platform.system()
        if sysplat == "Windows":
            self.mplayer.set_hwnd(winid)
        elif sysplat == "Darwin":
            self.mplayer.set_nsobject(winid)
        else:
            self.mplayer.set_xwindow(winid)

        self.mplayer.play()
        self.timer.start()

        # Start full screen
        self.showFullScreen()

    def toggle_play(self):
        if self.mplayer.is_playing():
            self.mplayer.pause()
            self.play_btn.setText("▶ Play")
        else:
            self.mplayer.play()
            self.play_btn.setText("⏸ Pause")

    def set_position(self, pos):
        self.mplayer.set_position(pos / 1000.0)

    def update_slider(self):
        if self.mplayer.is_playing():
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(int(self.mplayer.get_position() * 1000))
            self.position_slider.blockSignals(False)

    def stop(self):
        self.mplayer.stop()
        self.timer.stop()


class CacheStore:
    def __init__(self, library_dir: Path):
        self.library_dir = library_dir
        self.cache_dir = library_dir / ".localflix"
        self.cache_dir.mkdir(exist_ok=True)
        (self.cache_dir / "posters").mkdir(exist_ok=True)
        self.meta_path = self.cache_dir / "metadata.json"
        self.config_path = self.cache_dir / "config.json"
        self._meta: Dict[str, Dict] = {}
        self._load()

    def _load(self):
        if self.meta_path.exists():
            try:
                self._meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            except Exception:
                self._meta = {}
        else:
            self._meta = {}

    def save(self):
        self.meta_path.write_text(json.dumps(self._meta, indent=2, ensure_ascii=False), encoding="utf-8")

    def get(self, movie_path: Path) -> Optional[MovieMeta]:
        rec = self._meta.get(str(movie_path))
        return MovieMeta.from_dict(rec) if rec else None

    def put(self, meta: MovieMeta):
        self._meta[str(Path(meta.path))] = meta.to_dict()
        self.save()

    def get_api_key(self) -> Optional[str]:
        if self.config_path.exists():
            try:
                cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
                key = cfg.get("tmdb_api_key")
                if key:
                    return key.strip()
            except Exception:
                pass
        return None

class TMDbClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("TMDB_API_KEY", "").strip()
        if not self.api_key:
            print("[WARN] TMDb API key not found in environment. Will attempt config file.")

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key and len(self.api_key) > 40 else {}

    def _params(self, extra: Optional[Dict] = None) -> Dict:
        p = {"language": "en-US"}
        if not self._headers():
            p["api_key"] = self.api_key
        if extra:
            p.update(extra)
        return p

    def search_movie(self, query: str, year: Optional[int]) -> Optional[Dict]:
        if not self.api_key:
            return None
        url = f"{TMDB_API_URL}/search/movie"
        params = self._params({"query": query, "include_adult": False})
        if year:
            params["year"] = year
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                return None
            query_lower = query.lower()
            best_score = 0
            best_match = None
            from rapidfuzz import fuzz
            for item in results:
                title = item.get("title", "").lower()
                score = fuzz.ratio(query_lower, title)
                if score > best_score:
                    best_score = score
                    best_match = item
            return best_match
        except Exception as e:
            print("TMDb search error:", e)
            return None

    def download_poster(self, poster_path: str, dest: Path) -> Optional[Path]:
        size = "w342"
        url = f"{TMDB_IMG_BASE}/{size}{poster_path}"
        try:
            with requests.get(url, stream=True, timeout=20) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            return dest
        except Exception as e:
            print("Poster download failed:", e)
            return None

# --------------------------- Filename Parsing ------------------------------ #

COMMON_TAGS = [
    r"1080p", r"2160p", r"720p", r"480p", r"x264", r"x265", r"h264", r"h265", r"10bit",
    r"BluRay", r"WEBRip", r"WEB-DL", r"HDRip", r"BRRip", r"DVDRip", r"HEVC", r"AAC", r"DTS",
    r"YTS", r"RARBG", r"EXTENDED", r"REMASTERED", r"PROPER", r"REPACK", r"IMAX",
]
TAG_RE = re.compile(r"\b(" + "|".join(COMMON_TAGS) + r")\b", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


def clean_title_from_filename_with_year(path: Path) -> Tuple[str, Optional[int]]:
    name = path.stem
    if guessit:
        try:
            g = guessit(name)
            title = str(g.get("title") or name)
            year = g.get("year")
            return title, int(year) if year else None
        except Exception:
            pass
    # Fallback heuristics
    s = re.sub(r"[._]+", " ", name)
    year_match = YEAR_RE.search(s)
    year = int(year_match.group(1)) if year_match else None
    s = TAG_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s, year

def clean_title_from_filename(path: Path) -> str:
    s = re.sub(r"[._]+", " ", path.stem)
    s = TAG_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s  # just return cleaned title



# --------------------------- Library Scanner ------------------------------- #

class LibraryScanner(QtCore.QObject):
    movie_found = QtCore.Signal(MovieMeta)
    finished = QtCore.Signal()

    def __init__(self, library_dir: Path, cache: CacheStore, tmdb: TMDbClient):
        super().__init__()
        self.library_dir = library_dir
        self.cache = cache
        self.tmdb = tmdb

    def scan(self):
        def work():
            for p in sorted(self.library_dir.rglob("*")):
                if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
                    cached = self.cache.get(p)
                    if cached:
                        self.movie_found.emit(cached)
                        continue
                    """
                    title, year = clean_title_from_filename(p)
                    meta = MovieMeta(path=str(p), title=title, year=year)
                    # Fetch metadata from TMDb
                    item = self.tmdb.search_movie(title, year)"""
                    title = clean_title_from_filename(p)
                    meta = MovieMeta(path=str(p), title=title)
                    item = self.tmdb.search_movie(title, None)  # ignore year

                    if item:
                        meta.tmdb_id = item.get("id")
                        meta.overview = item.get("overview")
                        poster_rel = item.get("poster_path")
                        if poster_rel:
                            poster_dest = self.cache.cache_dir / "posters" / f"{meta.tmdb_id}{Path(poster_rel).suffix or '.jpg'}"
                            if not poster_dest.exists():
                                saved = self.tmdb.download_poster(poster_rel, poster_dest)
                                if saved:
                                    meta.poster_path = str(saved)
                            else:
                                meta.poster_path = str(poster_dest)
                    self.cache.put(meta)
                    self.movie_found.emit(meta)
            self.finished.emit()

        t = threading.Thread(target=work, daemon=True)
        t.start()

# --------------------------- Main UI --------------------------------------- #

class MovieListItem(QtWidgets.QListWidgetItem):
    def __init__(self, meta: MovieMeta):
        super().__init__(meta.title)
        self.meta = meta
        if meta.poster_path and Path(meta.poster_path).exists():
            pix = QtGui.QPixmap(meta.poster_path)
            if not pix.isNull():
                icon = QtGui.QIcon(pix)
                self.setIcon(icon)


class LocalFlix(QtWidgets.QMainWindow):
    def __init__(self, library_dir: Path):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1200, 800)

        # --- Stacked widget (manages Library + Player) ---
        self.stacked = QtWidgets.QStackedWidget()
        self.setCentralWidget(self.stacked)   # <--- THIS stays central forever

        # --- Library Page ---
        self.library_page = QtWidgets.QWidget()
        lib_layout = QtWidgets.QVBoxLayout(self.library_page)

        # Search + list
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Search movies…")
        self.search_edit.textChanged.connect(self._apply_filter)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setViewMode(QtWidgets.QListView.IconMode)
        self.list_widget.setResizeMode(QtWidgets.QListView.Adjust)
        self.list_widget.setIconSize(QtCore.QSize(120, 180))
        self.list_widget.setGridSize(QtCore.QSize(140, 220))
        self.list_widget.setMovement(QtWidgets.QListView.Static)
        self.list_widget.itemSelectionChanged.connect(self._on_select)

        # Detail panel
        self.poster_label = QtWidgets.QLabel()
        self.poster_label.setFixedSize(300, 450)
        self.poster_label.setScaledContents(True)
        self.title_label = QtWidgets.QLabel("Title")
        self.title_label.setStyleSheet("font-size: 22px; font-weight: 600;")
        self.overview = QtWidgets.QTextEdit()
        self.overview.setReadOnly(True)
        self.play_btn = QtWidgets.QPushButton("▶ Play")
        self.play_btn.clicked.connect(self._play_selected)

        detail_layout = QtWidgets.QVBoxLayout()
        detail_layout.addWidget(self.title_label)
        detail_layout.addWidget(self.poster_label)
        detail_layout.addWidget(self.overview)
        detail_layout.addWidget(self.play_btn)
        detail_widget = QtWidgets.QWidget()
        detail_widget.setLayout(detail_layout)

        # Splitter for list + details
        splitter = QtWidgets.QSplitter()
        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.addWidget(self.search_edit)
        left_layout.addWidget(self.list_widget)
        splitter.addWidget(left)
        splitter.addWidget(detail_widget)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)

        lib_layout.addWidget(splitter)
        self.stacked.addWidget(self.library_page)

        # --- Player Page ---
        self.player_page = QtWidgets.QWidget()
        self.player_layout = QtWidgets.QVBoxLayout(self.player_page)
        self.player_layout.setContentsMargins(0, 0, 0, 0)

        # Create a container that will stretch
        self.player_container = QtWidgets.QWidget()
        self.player_container.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding
        )
        self.player_layout.addWidget(self.player_container)
        self.stacked.addWidget(self.player_page)

        # Show library first
        self.stacked.setCurrentWidget(self.library_page)

        # --- Rest of init ---
        self.library_dir = library_dir
        self.cache = CacheStore(library_dir)
        self.tmdb = TMDbClient(api_key=self.cache.get_api_key())

        # Menu
        bar = self.menuBar()
        file_menu = bar.addMenu("File")
        rescan_act = QtGui.QAction("Rescan Library", self)
        rescan_act.triggered.connect(self.rescan)
        file_menu.addAction(rescan_act)

        choose_dir_act = QtGui.QAction("Change Library Folder…", self)
        choose_dir_act.triggered.connect(self.choose_library_dir)
        file_menu.addAction(choose_dir_act)

        open_ext_act = QtGui.QAction("Play in external VLC", self)
        open_ext_act.triggered.connect(self._play_external)
        file_menu.addAction(open_ext_act)

        # Data
        self.all_items: List[MovieListItem] = []
        self.current_meta: Optional[MovieMeta] = None

        # Initial scan
        self.statusBar().showMessage(f"Scanning {self.library_dir} …")
        self.rescan()

    # ------------------- Library ops ------------------- #
    def rescan(self):
        self.list_widget.clear()
        self.all_items.clear()
        self.current_meta = None
        scanner = LibraryScanner(self.library_dir, self.cache, self.tmdb)
        scanner.movie_found.connect(self._add_movie_item)
        scanner.finished.connect(lambda: self.statusBar().showMessage("Scan complete."))
        scanner.scan()

    def _add_movie_item(self, meta: MovieMeta):
        item = MovieListItem(meta)
        self.all_items.append(item)
        self.list_widget.addItem(item)

    def _apply_filter(self, text: str):
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            it.setHidden(text.strip().lower() not in it.text().lower())

    def _on_select(self):
        items = self.list_widget.selectedItems()
        if not items:
            return
        item: MovieListItem = items[0]  # type: ignore
        self.current_meta = item.meta
        self.title_label.setText(f"{item.meta.title} {f'({item.meta.year})' if item.meta.year else ''}")
        if item.meta.poster_path and Path(item.meta.poster_path).exists():
            self.poster_label.setPixmap(QtGui.QPixmap(item.meta.poster_path))
        else:
            self.poster_label.setPixmap(QtGui.QPixmap())
        self.overview.setPlainText(item.meta.overview or "No description available.")

    # ------------------- Playback ------------------- #
    def _play_selected(self):
        if not self.current_meta:
            return
        try:
            self._ensure_player()
            self.player.play(self.current_meta.path)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Embedded VLC failed: {e}\nFalling back to external VLC.")
            self._play_external()

    def _ensure_player(self):
        if hasattr(self, "player"):
            return
        self.player = VLCPlayerWidget()
        self.dock = QtWidgets.QDockWidget("Player", self)
        self.dock.setWidget(self.player)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.dock)
        self.dock.show()

    def _play_external(self):
        if not self.current_meta:
            return
        path = self.current_meta.path
        # Try to open with system default (ideally VLC associated)
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore
            elif sys.platform == "darwin":
                os.system(f"open {shlex_quote(path)}")
            else:
                os.system(f"xdg-open {shlex_quote(path)}")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to open externally: {e}")

    def open_movie(self, movie_path: Path):
        # Remove old player if exists
        if hasattr(self, "player_widget"):
            self.player_widget.setParent(None)
            self.player_widget.deleteLater()

        # New player
        self.player_widget = VLCPlayerWidget()
        self.player_widget.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding
        )

        # Put inside the container
        player_layout = QtWidgets.QVBoxLayout(self.player_container)
        player_layout.setContentsMargins(0, 0, 0, 0)
        player_layout.addWidget(self.player_widget)

        # Switch view
        self.stacked.setCurrentWidget(self.player_page)
        self.showFullScreen()

        # Start movie
        self.player_widget.play(str(movie_path))


    def back_to_library(self):
        if hasattr(self, "player_widget"):
            self.player_widget.stop()
        self.stacked.setCurrentWidget(self.library_page)
        self.showNormal()


    # ------------------- Settings ------------------- #
    def choose_library_dir(self):
        new_dir = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose Movie Folder", str(self.library_dir))
        if new_dir:
            self.library_dir = Path(new_dir)
            self.cache = CacheStore(self.library_dir)
            self.rescan()


# --------------------------- Helpers --------------------------------------- #

def shlex_quote(s: str) -> str:
    # Minimal cross-platform shell quoting
    if platform.system() == "Windows":
        return f'"{s}"'
    import shlex
    return shlex.quote(s)


def select_library_dir_interactive() -> Optional[Path]:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    d = QtWidgets.QFileDialog.getExistingDirectory(None, "Choose your movies folder")
    if d:
        return Path(d)
    return None


# --------------------------- Main entry ------------------------------------ #

def load_or_choose_library_dir(cli_arg: Optional[str]) -> Optional[Path]:
    if cli_arg:
        p = Path(cli_arg).expanduser()
        if p.exists() and p.is_dir():
            return p
        else:
            print(f"Provided path does not exist or is not a directory: {p}")
    # If a .localflix/config.json exists adjacent to the provided dir, use it; else ask.
    # Simpler: always show dialog if no CLI arg
    return select_library_dir_interactive()


def main():
    # Reuse single Qt app instance
    app = QtWidgets.QApplication(sys.argv)

    # Get library directory
    cli_arg = sys.argv[1] if len(sys.argv) > 1 else None
    library_dir = load_or_choose_library_dir(cli_arg)
    if not library_dir:
        print("No library directory chosen; exiting.")
        return


    win = LocalFlix(library_dir)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
