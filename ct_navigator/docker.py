"""
CT Workflow - Docker Panel

Accordion architecture:
  Readability  — readability & composition inspection
  Value        — value organization observation (Squint)
  Color        — color organization observation (Squint Blur + placeholders)

Not a live navigator. Snapshot-based, active-capture only.
"""

import ctypes

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QEvent, QPoint, QRect, QSize
from PyQt5.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QCursor
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from krita import DockWidget, Krita, ManagedColor

from .effects import ImageEffects
from .palette_board import PaletteBoard
from .utils import logger

ZOOM_MIN = 0.2   # 20%  — 退远观察下限（再小则退化为缩略图）
ZOOM_MAX = 5.0   # 500% — 靠近观察上限


# ---------------------------------------------------------------------------
# Custom preview label that captures mouse-wheel for zoom and scrub-zoom
# ---------------------------------------------------------------------------
class _PreviewLabel(QLabel):
    """QLabel sub-class that forwards wheel events, click coordinates,
    and scrub-zoom (pen press + horizontal drag).

    sizeHint is pinned so that pixmap changes never inflate the parent layout.
    """

    wheel_zoomed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WA_TabletTracking, True)
        self.setMouseTracking(True)
        self.on_click = None          # callback(px, py)
        self.on_get_zoom = None       # callable() -> float
        self.on_set_zoom = None       # callable(float)
        self._scrubbing = False
        self._scrub_start_x = 0
        self._scrub_start_zoom = 1.0
        self._scrub_moved = False
        self._scrub_sensitivity = 0.005
        self._cursor_zoom = self._make_zoom_cursor()
        self._cursor_zoom_in = self._make_zoom_cursor('+')
        self._cursor_zoom_out = self._make_zoom_cursor('-')

    @staticmethod
    def _make_zoom_cursor(sign=None):
        pm = QPixmap(32, 32)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.setBrush(Qt.NoBrush)
        # Glass circle
        painter.drawEllipse(3, 3, 16, 16)
        # Handle
        painter.drawLine(17, 17, 26, 26)
        # Direction sign
        if sign == '+':
            painter.drawLine(11, 7, 11, 15)
            painter.drawLine(7, 11, 15, 11)
        elif sign == '-':
            painter.drawLine(7, 11, 15, 11)
        painter.end()
        return QCursor(pm, 11, 11)

    def sizeHint(self):
        # Fixed large size hint so the Accordion allocates enough space
        # for the preview regardless of zoom level. The actual display
        # size is determined by the parent layout; this is just a hint.
        return QSize(400, 300)

    def minimumSizeHint(self):
        # Pin minimumSizeHint — QLabel returns pixmap size by default,
        # which causes the Accordion to re-layout when zoom changes.
        return QSize(100, 80)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta != 0:
            self.wheel_zoomed.emit(delta)
        event.accept()

    def enterEvent(self, event):
        self.setCursor(self._cursor_zoom)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.unsetCursor()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        # Accept any mouse button — tablet drivers map pen tip to
        # LeftButton, MidButton, or even NoButton depending on config.
        logger.debug(
            f"PreviewLabel mousePress: button={event.button()} "
            f"buttons={event.buttons()} pos=({event.pos().x()},{event.pos().y()})"
        )
        if event.buttons() & (Qt.LeftButton | Qt.MidButton | Qt.RightButton):
            self.grabMouse()
            self._scrubbing = True
            self._scrub_start_x = event.globalX()
            self._scrub_moved = False
            if self.on_get_zoom:
                self._scrub_start_zoom = self.on_get_zoom()
            logger.debug(f"PreviewLabel scrub START zoom={self._scrub_start_zoom}")
            event.accept()
        elif self.on_click:
            self.on_click(event.pos().x(), event.pos().y())
            event.accept()

    def mouseMoveEvent(self, event):
        if self._scrubbing and self.on_set_zoom:
            delta_x = event.globalX() - self._scrub_start_x
            if abs(delta_x) > 3:
                self._scrub_moved = True
            if delta_x > 3:
                self.setCursor(self._cursor_zoom_in)
            elif delta_x < -3:
                self.setCursor(self._cursor_zoom_out)
            else:
                self.setCursor(self._cursor_zoom)
            new_zoom = self._scrub_start_zoom + delta_x * self._scrub_sensitivity
            new_zoom = max(ZOOM_MIN, min(ZOOM_MAX, new_zoom))
            logger.debug(f"PreviewLabel mouseMove: dx={delta_x} new_zoom={new_zoom:.3f}")
            self.on_set_zoom(new_zoom)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        logger.debug(
            f"PreviewLabel mouseRelease: button={event.button()} "
            f"scrubbing={self._scrubbing} moved={self._scrub_moved}"
        )
        if self._scrubbing:
            self.releaseMouse()
            self._scrubbing = False
            if not self._scrub_moved and self.on_click:
                self.on_click(event.pos().x(), event.pos().y())
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def tabletEvent(self, event):
        logger.debug(
            f"PreviewLabel tabletEvent: type={event.type()} "
            f"pos=({event.pos().x()},{event.pos().y()}) "
            f"pressure={event.pressure():.3f}"
        )
        # Ignore tablet events so Qt converts them to mouse events.
        # Krita’s tablet handling is inconsistent across drivers; uniform
        # mouse-event processing is more reliable.
        event.ignore()


# ---------------------------------------------------------------------------
# Tablet-aware button (Wintab pens do not always produce mouse events)
# ---------------------------------------------------------------------------
class _TabletButton(QPushButton):
    """QPushButton that also responds to tablet press/release.

    Wintab drivers inside Krita do not always convert tablet events to
    mouse events, so a standard QPushButton may completely miss pen taps.
    """

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._tablet_down = False

    def tabletEvent(self, event):
        et = event.type()
        if et == QEvent.TabletPress:
            self._tablet_down = True
            self.pressed.emit()
            event.accept()
        elif et == QEvent.TabletMove:
            event.ignore()
        elif et == QEvent.TabletRelease:
            if self._tablet_down:
                self._tablet_down = False
                self.released.emit()
                self.clicked.emit()
            event.accept()
        else:
            event.ignore()

    def mousePressEvent(self, event):
        # Accept any mouse button — tablet drivers map pen tip to
        # LeftButton, MidButton, or even NoButton depending on config.
        if event.buttons() & (Qt.LeftButton | Qt.MidButton | Qt.RightButton):
            super().mousePressEvent(event)
        else:
            event.accept()


# ---------------------------------------------------------------------------
# About Dialog
# ---------------------------------------------------------------------------
class _AboutDialog(QDialog):
    """CT Workflow About dialog. Fixed size, scrollable for future expansion."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About CT Workflow")
        self.setFixedSize(360, 480)
        self.setStyleSheet("background-color: #2a2a2a; color: #ccc;")

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignCenter)

        # Logo
        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        try:
            import os
            logo_path = os.path.join(os.path.dirname(__file__), "logo.png")
            pm = QPixmap(logo_path)
            if not pm.isNull():
                target_h = 120
                scaled = pm.scaledToHeight(target_h, Qt.SmoothTransformation)
                logo.setPixmap(scaled)
        except Exception:
            pass
        layout.addWidget(logo)

        # Title
        title = QLabel("CT Workflow")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #fff;")
        layout.addWidget(title)

        # Slogan
        slogan = QLabel("See structure.\nSee value.\nSee color.")
        slogan.setAlignment(Qt.AlignCenter)
        slogan.setStyleSheet("font-size: 12px; color: #999; line-height: 1.4;")
        layout.addWidget(slogan)

        # Description
        desc = QLabel("A visual inspection workflow\nfor painters and concept artists.")
        desc.setAlignment(Qt.AlignCenter)
        desc.setStyleSheet("font-size: 11px; color: #aaa;")
        layout.addWidget(desc)

        # Divider
        layout.addWidget(self._divider())

        # Created by
        created = QLabel("Created by")
        created.setAlignment(Qt.AlignCenter)
        created.setStyleSheet("font-size: 10px; color: #666;")
        layout.addWidget(created)

        author = QLabel("Minfang Zhou")
        author.setAlignment(Qt.AlignCenter)
        author.setStyleSheet("font-size: 13px; font-weight: bold; color: #ddd;")
        layout.addWidget(author)

        roles = QLabel("Concept Artist\nIllustrator\nAnimator\nAI Visual Researcher")
        roles.setAlignment(Qt.AlignCenter)
        roles.setStyleSheet("font-size: 10px; color: #888;")
        layout.addWidget(roles)

        # Divider
        layout.addWidget(self._divider())

        # Version
        version = QLabel("Version 1.0")
        version.setAlignment(Qt.AlignCenter)
        version.setStyleSheet("font-size: 10px; color: #777;")
        layout.addWidget(version)

        # License
        license_lbl = QLabel("MIT License")
        license_lbl.setAlignment(Qt.AlignCenter)
        license_lbl.setStyleSheet("font-size: 10px; color: #777;")
        layout.addWidget(license_lbl)

        # GitHub link
        github = QLabel(
            '<a href="https://github.com/" style="color: #4a9eff;">'
            "GitHub Repository</a>"
        )
        github.setAlignment(Qt.AlignCenter)
        github.setOpenExternalLinks(True)
        github.setStyleSheet("font-size: 10px;")
        layout.addWidget(github)

        layout.addStretch(1)
        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    @staticmethod
    def _divider():
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("color: #444;")
        return line


# ---------------------------------------------------------------------------
# Accordion Stage Panel
# ---------------------------------------------------------------------------
class _StagePanel(QWidget):
    """Single collapsible stage for the CT Inspection Accordion."""

    toggled = pyqtSignal()

    def __init__(self, title, index, parent=None):
        super().__init__(parent)
        self._title = title
        self._index = index
        self._expanded = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = QPushButton(self)
        self._header.setCheckable(True)
        self._header.setCursor(Qt.PointingHandCursor)
        self._header.setStyleSheet(
            "QPushButton { text-align: left; padding: 6px 8px; "
            "background-color: #333; color: #ccc; border: none; "
            "font-size: 12px; }"
            "QPushButton:checked { background-color: #3a3a3a; color: #fff; }"
            "QPushButton:hover { background-color: #444; }"
        )
        self._header.clicked.connect(self._on_header_clicked)
        layout.addWidget(self._header)

        self._content = QWidget(self)
        layout.addWidget(self._content)

        self._set_expanded(False)

    def _on_header_clicked(self):
        if not self._expanded:
            self.toggled.emit()

    def _set_expanded(self, expanded):
        self._expanded = expanded
        self._header.setChecked(expanded)
        arrow = "▼" if expanded else "▶"
        self._header.setText(f"{arrow}  {self._index} {self._title}")
        self._content.setVisible(expanded)

    def set_expanded(self, expanded):
        self._set_expanded(expanded)

    def content(self):
        return self._content


class CTAccordion(QWidget):
    """Accordion widget for CT Inspection Workflow.

    Only one stage expanded at a time. Emits stage_changed(index)
    when the user switches stages (0=Structure, 1=Value, 2=Color).
    """

    stage_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._panels = []
        titles = ["Readability", "Value", "Color"]
        for i, title in enumerate(titles):
            panel = _StagePanel(title, i + 1, self)
            panel.toggled.connect(lambda idx=i: self._on_panel_toggled(idx))
            layout.addWidget(panel)
            self._panels.append(panel)

        layout.addStretch(1)

        self._current = 0
        self._panels[0].set_expanded(True)

    def _on_panel_toggled(self, index):
        if index == self._current:
            return
        self._panels[self._current].set_expanded(False)
        self._panels[index].set_expanded(True)
        self._current = index
        self.stage_changed.emit(index)

    def current_index(self):
        return self._current

    def set_current_index(self, index):
        if 0 <= index < len(self._panels) and index != self._current:
            self._on_panel_toggled(index)

    def panel_content(self, index):
        return self._panels[index].content()


# ---------------------------------------------------------------------------
# Main Docker
# ---------------------------------------------------------------------------
class CTNavigatorDocker(DockWidget):
    """
    CT Workflow Docker Panel.

    Three stages:
      Readability — H-Flip, V-Flip, Desat, Invert, Binarize + Threshold
      Value       — Squint with depth control
      Color       — Original / Squint Blur + Palette Board placeholders
    """

    MAX_BASE_SIZE = 1024
    BOARD_MIN_HEIGHT = 120

    # ---- Lifecycle ---------------------------------------------------------

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CT Workflow")
        self.setMinimumSize(280, 480)

        # --- Structure effects ---
        self._readability_effects = {
            "hflip": False,
            "vflip": False,
            "desat": False,
            "invert": False,
            "binarize": False,
            "threshold": 96,
        }

        # --- Value effects ---
        self._value_effects = {
            "squint_depth": 0,  # 0 = clear desaturated, 8 = deep blur
        }

        # --- Color effects ---
        self._color_effects = {
            "mode": "original",  # "original" | "squint_blur"
        }

        # --- Frozen board canvas (Color tab only) ---
        self._frozen_board_canvas = None
        self._saved_board_canvas = None

        # --- Canvas state ---
        self._base_image: QImage | None = None
        self._current_doc_name: str = ""
        self._zoom_scale: float = 1.0
        self._zoom_min: float = ZOOM_MIN
        self._zoom_max: float = ZOOM_MAX
        self._zoom_step: float = 0.1

        # --- Per-tab frozen snapshots ---
        self._frozen_images = {
            "readability": None,
            "value": None,
            "color": None,
        }

        # --- Pen scrub zoom: app-level event filter + hardware poll fallback ---
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_pen_state)
        self._poll_timer.start(20)  # 50fps: slightly slower to reduce flicker
        self._poll_scrubbing = False
        self._poll_start_x = 0
        self._poll_start_zoom = 1.0
        self._poll_moved = False

        # Event-filter for TabletEvent / MouseEvent (catches Wintab pen input
        # before Krita canvas sees it)
        self._ef_scrubbing = False
        self._ef_start_x = 0
        self._ef_start_zoom = 1.0
        self._ef_moved = False
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)
        self._frozen_doc_names = {
            "readability": "",
            "value": "",
            "color": "",
        }
        self._is_holding: bool = False
        self._last_pixmap: QPixmap | None = None

        # --- Resize drag state (right-side grip) ---
        self._resize_dragging = False
        self._resize_start_x = 0
        self._resize_start_width = 0

        # --- Build UI ---
        self._build_ui()
        self._setup_auto_refresh()
        self._setup_resize_debounce()

        # Initial capture
        self.capture_preview()

    # ---- Convenience properties --------------------------------------------

    @property
    def _current_tab(self) -> str:
        return ["readability", "value", "color"][self._accordion.current_index()]

    @property
    def _frozen_image(self):
        return self._frozen_images.get(self._current_tab)

    @_frozen_image.setter
    def _frozen_image(self, value):
        self._frozen_images[self._current_tab] = value

    @property
    def _frozen_doc_name(self) -> str:
        return self._frozen_doc_names.get(self._current_tab, "")

    @_frozen_doc_name.setter
    def _frozen_doc_name(self, value: str):
        self._frozen_doc_names[self._current_tab] = value

    def _active_preview(self):
        idx = self._accordion.current_index()
        previews = [
            getattr(self, '_preview_readability', None),
            getattr(self, '_preview_value', None),
            getattr(self, '_preview_color', None),
        ]
        if 0 <= idx < len(previews):
            return previews[idx]
        return None

    # ---- UI Construction ---------------------------------------------------

    def _build_ui(self):
        container = QWidget(self)
        self.setWidget(container)

        # Main horizontal: content | resize grip
        main = QHBoxLayout(container)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        # Content area
        content = QWidget(container)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # -- Header: Actions --
        actions = QHBoxLayout()
        actions.setSpacing(6)

        self._capture_btn = _TabletButton("Capture", self)
        self._capture_btn.setToolTip("Snapshot current canvas")
        self._capture_btn.clicked.connect(self.capture_preview)
        actions.addWidget(self._capture_btn)

        self._freeze_btn = _TabletButton("Freeze", self)
        self._freeze_btn.setToolTip("Lock current preview")
        self._freeze_btn.clicked.connect(self._on_freeze)
        actions.addWidget(self._freeze_btn)

        self._hold_btn = _TabletButton("Hold", self)
        self._hold_btn.setToolTip("Momentary compare")
        self._hold_btn.pressed.connect(self._on_hold_pressed)
        self._hold_btn.released.connect(self._on_hold_released)
        actions.addWidget(self._hold_btn)

        self._fit_btn = _TabletButton("Fit", self)
        self._fit_btn.setToolTip("Reset zoom to fit panel")
        self._fit_btn.clicked.connect(self._reset_zoom)
        actions.addWidget(self._fit_btn)

        actions.addStretch(1)

        self._about_btn = _TabletButton("ⓘ", self)
        self._about_btn.setToolTip("About CT Workflow")
        self._about_btn.setFixedWidth(28)
        self._about_btn.clicked.connect(self._show_about)
        actions.addWidget(self._about_btn)

        layout.addLayout(actions)

        # -- Header: Status --
        status = QHBoxLayout()
        status.setSpacing(6)

        self._status_label = QLabel("Ready", self)
        self._status_label.setStyleSheet("color: #888; font-size: 11px;")
        status.addWidget(self._status_label, stretch=1)

        self._zoom_label = QLabel("100 %", self)
        self._zoom_label.setStyleSheet(
            "color: #888; font-size: 11px; min-width: 42px;"
        )
        self._zoom_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        status.addWidget(self._zoom_label)

        layout.addLayout(status)

        # -- Accordion Workflow --
        self._accordion = CTAccordion(content)
        self._build_readability_stage(self._accordion.panel_content(0))
        self._build_value_stage(self._accordion.panel_content(1))
        self._build_color_stage(self._accordion.panel_content(2))
        self._accordion.stage_changed.connect(self._on_stage_changed)
        layout.addWidget(self._accordion, stretch=1)

        main.addWidget(content, stretch=1)

        # -- Right-side resize grip --
        self._resize_bar = QWidget(container)
        self._resize_bar.setFixedWidth(8)
        self._resize_bar.setCursor(Qt.SizeHorCursor)
        self._resize_bar.setStyleSheet("background-color: #555555;")
        self._resize_bar.installEventFilter(self)
        main.addWidget(self._resize_bar)

        # -- Bottom resize grip --
        self._bottom_bar = QWidget(container)
        self._bottom_bar.setFixedHeight(8)
        self._bottom_bar.setCursor(Qt.SizeVerCursor)
        self._bottom_bar.setStyleSheet("background-color: #555555;")
        self._bottom_bar.installEventFilter(self)
        layout.addWidget(self._bottom_bar)

    def _build_readability_stage(self, parent):
        """Readability stage: H-Flip, V-Flip, Desat, Invert, Binarize + Threshold."""
        page_layout = QVBoxLayout(parent)
        page_layout.setContentsMargins(8, 8, 8, 8)
        page_layout.setSpacing(6)

        # Effect buttons row
        fx = QHBoxLayout()
        fx.setSpacing(6)

        self._hflip_btn = QPushButton("H-Flip", parent)
        self._hflip_btn.setCheckable(True)
        self._hflip_btn.setToolTip("Toggle horizontal flip")
        self._hflip_btn.clicked.connect(lambda c: self._set_readability_effect("hflip", c))
        fx.addWidget(self._hflip_btn)

        self._vflip_btn = QPushButton("V-Flip", parent)
        self._vflip_btn.setCheckable(True)
        self._vflip_btn.setToolTip("Toggle vertical flip")
        self._vflip_btn.clicked.connect(lambda c: self._set_readability_effect("vflip", c))
        fx.addWidget(self._vflip_btn)

        self._desat_btn = QPushButton("Desat", parent)
        self._desat_btn.setCheckable(True)
        self._desat_btn.setToolTip("Remove colour, observe value only")
        self._desat_btn.clicked.connect(lambda c: self._set_readability_effect("desat", c))
        fx.addWidget(self._desat_btn)

        self._invert_btn = QPushButton("Invert", parent)
        self._invert_btn.setCheckable(True)
        self._invert_btn.setToolTip("Toggle invert")
        self._invert_btn.clicked.connect(lambda c: self._set_readability_effect("invert", c))
        fx.addWidget(self._invert_btn)

        self._bin_btn = QPushButton("Binarize", parent)
        self._bin_btn.setCheckable(True)
        self._bin_btn.setToolTip("Toggle binarize")
        self._bin_btn.clicked.connect(lambda c: self._set_readability_effect("binarize", c))
        fx.addWidget(self._bin_btn)

        page_layout.addLayout(fx)

        # Threshold slider
        th_layout = QHBoxLayout()
        th_layout.setSpacing(6)

        th_label = QLabel("Threshold", parent)
        th_layout.addWidget(th_label)

        self._threshold_slider = QSlider(Qt.Horizontal, parent)
        self._threshold_slider.setRange(64, 128)
        self._threshold_slider.setValue(96)
        self._threshold_slider.setSingleStep(1)
        self._threshold_slider.setEnabled(False)
        self._threshold_slider.valueChanged.connect(self._on_threshold_changed)
        th_layout.addWidget(self._threshold_slider, stretch=1)

        self._threshold_value_label = QLabel("96", parent)
        self._threshold_value_label.setStyleSheet(
            "color: #888; font-size: 11px; min-width: 24px;"
        )
        self._threshold_value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        th_layout.addWidget(self._threshold_value_label)

        page_layout.addLayout(th_layout)

        # Preview
        self._preview_readability = _PreviewLabel(parent)
        self._preview_readability.setStyleSheet(
            "QLabel { background-color: #2a2a2a; border: 1px solid #555; }"
        )
        self._preview_readability.setMinimumSize(100, 80)
        self._preview_readability.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        self._preview_readability.wheel_zoomed.connect(self._on_wheel_zoomed)
        self._preview_readability.on_get_zoom = lambda: self._zoom_scale
        self._preview_readability.on_set_zoom = self._on_scrub_zoomed
        page_layout.addWidget(self._preview_readability, stretch=1)

    def _build_value_stage(self, parent):
        """Value stage: auto-desaturated, depth slider controls blur."""
        page_layout = QVBoxLayout(parent)
        page_layout.setContentsMargins(8, 8, 8, 8)
        page_layout.setSpacing(6)

        # Hint label
        hint = QLabel("Desaturated by default. Depth controls blur.", parent)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        page_layout.addWidget(hint)

        # Depth slider
        sd_layout = QHBoxLayout()
        sd_layout.setSpacing(6)

        sd_label = QLabel("Depth", parent)
        sd_layout.addWidget(sd_label)

        self._squint_slider = QSlider(Qt.Horizontal, parent)
        self._squint_slider.setRange(0, 8)
        self._squint_slider.setValue(0)
        self._squint_slider.setSingleStep(1)
        self._squint_slider.valueChanged.connect(self._on_squint_depth_changed)
        sd_layout.addWidget(self._squint_slider, stretch=1)

        self._squint_depth_label = QLabel(
            "Clear ─────●───── Deep", parent
        )
        self._squint_depth_label.setStyleSheet(
            "color: #888; font-size: 11px;"
        )
        sd_layout.addWidget(self._squint_depth_label)

        page_layout.addLayout(sd_layout)

        # Observation prompt
        self._observation_label = QLabel(parent)
        self._observation_label.setStyleSheet("color: #888; font-size: 11px;")
        self._observation_label.setWordWrap(True)
        self._update_observation_prompt()
        page_layout.addWidget(self._observation_label)

        # Preview
        self._preview_value = _PreviewLabel(parent)
        self._preview_value.setStyleSheet(
            "QLabel { background-color: #2a2a2a; border: 1px solid #555; }"
        )
        self._preview_value.setMinimumSize(100, 80)
        self._preview_value.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        self._preview_value.wheel_zoomed.connect(self._on_wheel_zoomed)
        self._preview_value.on_get_zoom = lambda: self._zoom_scale
        self._preview_value.on_set_zoom = self._on_scrub_zoomed
        page_layout.addWidget(self._preview_value, stretch=1)

    def _build_color_stage(self, parent):
        """Color stage: Original / Squint Blur + Palette Board."""
        page_layout = QVBoxLayout(parent)
        page_layout.setContentsMargins(8, 8, 8, 8)
        page_layout.setSpacing(6)

        # Mode radio buttons
        mode_layout = QHBoxLayout()
        mode_layout.setSpacing(6)

        mode_label = QLabel("Mode", parent)
        mode_layout.addWidget(mode_label)

        self._color_mode_group = QButtonGroup(parent)

        self._radio_original = QRadioButton("Original", parent)
        self._radio_original.setChecked(True)
        self._radio_original.toggled.connect(
            lambda c: self._on_color_mode_changed("original", c)
        )
        self._color_mode_group.addButton(self._radio_original)
        mode_layout.addWidget(self._radio_original)

        self._radio_squint_blur = QRadioButton("Squint Blur", parent)
        self._radio_squint_blur.toggled.connect(
            lambda c: self._on_color_mode_changed("squint_blur", c)
        )
        self._color_mode_group.addButton(self._radio_squint_blur)
        mode_layout.addWidget(self._radio_squint_blur)

        mode_layout.addStretch(1)
        page_layout.addLayout(mode_layout)

        # Preview
        self._preview_color = _PreviewLabel(parent)
        self._preview_color.setStyleSheet(
            "QLabel { background-color: #2a2a2a; border: 1px solid #555; "
            "color: #888; font-size: 11px; }"
        )
        self._preview_color.setMinimumSize(100, 100)
        self._preview_color.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        self._preview_color.wheel_zoomed.connect(self._on_wheel_zoomed)
        self._preview_color.on_get_zoom = lambda: self._zoom_scale
        self._preview_color.on_set_zoom = self._on_scrub_zoomed
        page_layout.addWidget(self._preview_color, stretch=2)

        # Breathing gap between Preview and Board (4px, non-draggable)
        gap = QWidget(parent)
        gap.setFixedHeight(4)
        gap.setStyleSheet("background-color: #333333;")
        page_layout.addWidget(gap)

        # Palette Board (color provider + setter injected from docker)
        self._board = PaletteBoard(
            parent,
            color_provider=self._get_current_color,
            color_setter=self._set_current_color,
        )
        self._board.setMinimumHeight(120)
        page_layout.addWidget(self._board, stretch=1)

        # Controls: Clear / Brush Size / Color Indicator
        controls = QHBoxLayout()
        controls.setSpacing(6)

        self._clear_board_btn = QPushButton("Clear", parent)
        self._clear_board_btn.setToolTip("Clear board")
        self._clear_board_btn.clicked.connect(self._on_clear_board)
        controls.addWidget(self._clear_board_btn)

        # Fixed brush-size buttons (mutually exclusive)
        self._brush_group = QButtonGroup(parent)
        self._brush_group.setExclusive(True)
        for px in (80, 60, 40, 20):
            btn = _TabletButton(str(px), parent)
            btn.setCheckable(True)
            btn.setFixedWidth(36)
            btn.setStyleSheet(
                "QPushButton { font-size: 10px; padding: 2px; "
                "background-color: #444; color: #ccc; border: 1px solid #555; }"
                "QPushButton:checked { background-color: #4a9eff; color: #fff; "
                "border: 1px solid #4a9eff; }"
                "QPushButton:hover { background-color: #555; }"
            )
            btn.clicked.connect(lambda checked, p=px: self._on_brush_size_changed(p))
            self._brush_group.addButton(btn)
            controls.addWidget(btn)
            if px == 20:
                btn.setChecked(True)

        # Eyedropper hint icon
        self._picker_icon = QLabel("🖉", parent)
        self._picker_icon.setStyleSheet(
            "QLabel { font-size: 14px; color: #ccc; }"
            "QToolTip { color: #000000; background-color: #ffffff; border: 1px solid #cccccc; }"
        )
        self._picker_icon.setToolTip("Shift+Click")
        controls.addWidget(self._picker_icon)

        # External color indicator (moved out of Board paintEvent)
        self._color_indicator = QLabel(parent)
        self._color_indicator.setFixedSize(20, 20)
        self._color_indicator.setStyleSheet(
            "background-color: #ffffff; border: 1px solid #555;"
        )
        self._color_indicator.setToolTip("Current Krita foreground color")
        controls.addWidget(self._color_indicator)

        controls.addStretch(1)
        page_layout.addLayout(controls)

        # Hint
        hint = QLabel("Select color in Krita, then paint here", parent)
        hint.setStyleSheet("color: #666; font-size: 10px;")
        hint.setAlignment(Qt.AlignCenter)
        page_layout.addWidget(hint)

    # ---- Stage switching ---------------------------------------------------

    def _on_stage_changed(self, index: int):
        # Release hold when switching stages
        if self._is_holding:
            self._is_holding = False
            self._hold_btn.setStyleSheet("")

        self._update_status()
        self._update_preview()

    # ---- Auto refresh (disabled) -------------------------------------------

    def _setup_auto_refresh(self):
        pass

    def _setup_resize_debounce(self):
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(100)
        self._resize_timer.timeout.connect(self._update_preview)

    # ---- Canvas changes ----------------------------------------------------

    def canvasChanged(self, canvas):
        try:
            if canvas is None:
                self._show_placeholder("No document open")
                self._status_label.setText("No document")
                self._base_image = None
                self._current_doc_name = ""
                self._clear_all_frozen()
            else:
                doc = Krita.instance().activeDocument()
                if doc is not None and doc.name() != self._current_doc_name:
                    self._base_image = None
                    self._current_doc_name = ""
                    self._clear_all_frozen()
                    self._show_placeholder("Capture required")
                    self._status_label.setText(
                        "Document changed — Capture required"
                    )
        except Exception as e:
            logger.error(f"canvasChanged: {e}")

    # ---- Capture -----------------------------------------------------------

    def capture_preview(self):
        app = Krita.instance()
        if app is None:
            self._show_placeholder("Krita not ready")
            return

        doc = app.activeDocument()
        if doc is None:
            self._show_placeholder("No document open")
            self._status_label.setText("No document")
            self._base_image = None
            self._clear_all_frozen()
            return

        doc_name = doc.name()
        if doc_name != self._current_doc_name:
            self._clear_all_frozen()

        try:
            bounds = doc.bounds()
            if bounds is not None and bounds.width() > 0 and bounds.height() > 0:
                proj = doc.projection(
                    bounds.x(), bounds.y(), bounds.width(), bounds.height()
                )
            else:
                proj = doc.projection(0, 0, doc.width(), doc.height())
            if proj is None or proj.isNull():
                self._show_placeholder("Blank canvas")
                self._status_label.setText("Blank canvas")
                self._base_image = None
                return
        except Exception as e:
            logger.error(f"projection: {e}")
            self._show_placeholder(f"Error: {e}")
            return

        image = proj.copy()
        if image.isNull() or image.width() == 0 or image.height() == 0:
            self._show_placeholder("Blank canvas")
            self._status_label.setText("Blank canvas")
            self._base_image = None
            return

        if self._is_blank(image):
            self._show_placeholder("Blank canvas")
            self._status_label.setText("Blank canvas")
            self._base_image = image
            self._update_preview()
            return

        if (
            image.width() > self.MAX_BASE_SIZE
            or image.height() > self.MAX_BASE_SIZE
        ):
            image = image.scaled(
                self.MAX_BASE_SIZE,
                self.MAX_BASE_SIZE,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )

        self._base_image = image
        self._current_doc_name = doc_name
        self._update_status()
        self._update_preview()

    def _is_blank(self, image: QImage) -> bool:
        w, h = image.width(), image.height()
        if w < 2 or h < 2:
            return True
        try:
            c1 = image.pixelColor(0, 0)
            c2 = image.pixelColor(w // 2, h // 2)
            c3 = image.pixelColor(w - 1, h - 1)
            if (
                (c1.red(), c1.green(), c1.blue())
                == (c2.red(), c2.green(), c2.blue())
                == (c3.red(), c3.green(), c3.blue())
            ):
                return True
        except Exception:
            pass
        return False

    # ---- Preview Rendering -------------------------------------------------

    def _compute_target_size(self, image: QImage) -> tuple[int, int]:
        preview = self._active_preview()
        if preview is None:
            return 100, 100
        label_w = max(preview.width() - 4, 50)
        label_h = max(preview.height() - 4, 50)

        if self._zoom_scale == 1.0:
            return label_w, label_h

        img_w, img_h = image.width(), image.height()
        fit_scale = min(
            label_w / max(img_w, 1), label_h / max(img_h, 1)
        )
        final_scale = fit_scale * self._zoom_scale
        target_w = int(img_w * final_scale)
        target_h = int(img_h * final_scale)
        target_w = min(max(target_w, 10), 2048)
        target_h = min(max(target_h, 10), 2048)
        return target_w, target_h

    def _update_preview(self):
        if self._base_image is None:
            self._show_placeholder("Capture required")
            return
        if self._is_holding:
            return

        try:
            target_w, target_h = self._compute_target_size(self._base_image)

            thumb = self._base_image.scaled(
                target_w,
                target_h,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )

            tab = self._current_tab
            if tab == "readability":
                result = ImageEffects.apply(
                    thumb,
                    hflip=self._readability_effects["hflip"],
                    vflip=self._readability_effects["vflip"],
                    desat=self._readability_effects["desat"],
                    invert=self._readability_effects["invert"],
                    binarize=self._readability_effects["binarize"],
                    binarize_threshold=self._readability_effects["threshold"],
                )
            elif tab == "value":
                result = ImageEffects.apply_value(
                    thumb,
                    squint_depth=self._value_effects["squint_depth"],
                )
            else:
                if self._color_effects["mode"] == "squint_blur":
                    result = ImageEffects.apply_color(
                        thumb, squint_blur=True
                    )
                else:
                    result = QImage(thumb)

            self._last_pixmap = QPixmap.fromImage(result)
            preview = self._active_preview()
            if preview is not None:
                preview.setPixmap(self._last_pixmap)
            self._zoom_label.setText(f"{int(self._zoom_scale * 100)} %")

            # Update external color indicator when in Color stage
            if tab == "color" and hasattr(self, '_color_indicator'):
                try:
                    c = self._get_current_color()
                    self._color_indicator.setStyleSheet(
                        f"background-color: rgb({c.red()},{c.green()},{c.blue()}); "
                        "border: 1px solid #555;"
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"_update_preview: {e}")
            self._status_label.setText("Render error")

    def _render_frozen_display(self):
        frozen = self._frozen_image
        if frozen is None or frozen.isNull():
            return

        try:
            target_w, target_h = self._compute_target_size(frozen)
            scaled = frozen.scaled(
                target_w,
                target_h,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            preview = self._active_preview()
            if preview is not None:
                preview.setPixmap(QPixmap.fromImage(scaled))
                preview.repaint()
            self._zoom_label.setText(f"{int(self._zoom_scale * 100)} %")
        except Exception as e:
            logger.error(f"_render_frozen_display: {e}")

    def _show_placeholder(self, text: str):
        preview = self._active_preview()
        if preview is None:
            return
        preview.setText(text)
        preview.setPixmap(QPixmap())

    def _update_status(self):
        if self._base_image is None:
            return
        base = (
            f"{self._current_doc_name}  "
            f"{self._base_image.width()}x{self._base_image.height()}"
        )
        if self._is_holding:
            self._status_label.setText(f"{base}  [FROZEN]")
        elif self._frozen_image is not None:
            self._status_label.setText(f"{base}  [● Frozen]")
        else:
            self._status_label.setText(base)

    # ---- Readability effect toggles ----------------------------------------

    def _set_readability_effect(self, key: str, checked: bool):
        self._readability_effects[key] = checked
        if key == "binarize":
            self._threshold_slider.setEnabled(checked)
        self._update_preview()

    def _on_threshold_changed(self, value: int):
        self._readability_effects["threshold"] = value
        self._threshold_value_label.setText(str(value))
        if self._readability_effects["binarize"]:
            self._update_preview()

    # ---- Value effect toggles ----------------------------------------------

    def _on_squint_depth_changed(self, value: int):
        self._value_effects["squint_depth"] = value
        self._update_observation_prompt()
        self._update_preview()

    def _update_observation_prompt(self):
        depth = self._value_effects["squint_depth"]
        if depth == 0:
            self._observation_label.setText("色彩被移除后，主体是否仍成立？")
        else:
            self._observation_label.setText(
                "眯眼后，大形是否仍然稳定？"
            )

    # ---- Color effect toggles ----------------------------------------------

    def _on_color_mode_changed(self, mode: str, checked: bool):
        if not checked:
            return
        self._color_effects["mode"] = mode
        self._update_preview()

    # ---- Freeze / Hold -----------------------------------------------------

    def _on_freeze(self):
        if self._base_image is None or self._base_image.isNull():
            self._clear_frozen()
            return

        tab = self._current_tab
        if tab == "readability":
            frozen_result = ImageEffects.apply(
                self._base_image,
                hflip=self._readability_effects["hflip"],
                vflip=self._readability_effects["vflip"],
                desat=self._readability_effects["desat"],
                invert=self._readability_effects["invert"],
                binarize=self._readability_effects["binarize"],
                binarize_threshold=self._readability_effects["threshold"],
            )
        elif tab == "value":
            frozen_result = ImageEffects.apply_value(
                self._base_image,
                squint_depth=self._value_effects["squint_depth"],
            )
        else:
            if self._color_effects["mode"] == "squint_blur":
                frozen_result = ImageEffects.apply_color(
                    self._base_image, squint_blur=True
                )
            else:
                frozen_result = QImage(self._base_image)

        self._frozen_image = frozen_result.copy()
        self._frozen_doc_name = self._current_doc_name

        # Freeze board canvas in Color tab
        if tab == "color":
            self._frozen_board_canvas = self._board.get_canvas()

        self._update_status()

    def _clear_frozen(self):
        tab = self._current_tab
        self._frozen_images[tab] = None
        self._frozen_doc_names[tab] = ""
        if tab == "color":
            self._frozen_board_canvas = None
        self._update_status()

    def _clear_all_frozen(self):
        for key in self._frozen_images:
            self._frozen_images[key] = None
            self._frozen_doc_names[key] = ""
        self._frozen_board_canvas = None
        self._update_status()

    def _on_hold_pressed(self):
        frozen = self._frozen_image
        if frozen is not None and not frozen.isNull():
            self._is_holding = True
            self._render_frozen_display()
            self._hold_btn.setStyleSheet(
                "QPushButton { background-color: #4a9eff; "
                "color: white; border: none; padding: 4px 12px; }"
            )
            # Save current board canvas and show frozen canvas in Color tab
            if self._current_tab == "color":
                self._saved_board_canvas = self._board.get_canvas()
                if self._frozen_board_canvas is not None:
                    self._board.set_canvas(self._frozen_board_canvas)
            self._update_status()
        else:
            self._show_placeholder("No snapshot")
            preview = self._active_preview()
            if preview is not None:
                preview.repaint()
            self._hold_btn.setStyleSheet("")
            self._update_status()

    def _on_hold_released(self):
        self._is_holding = False
        self._hold_btn.setStyleSheet("")
        if self._base_image is not None:
            self._update_preview()
        # Restore current board canvas in Color tab
        if self._current_tab == "color" and self._saved_board_canvas is not None:
            self._board.set_canvas(self._saved_board_canvas)
        self._update_status()

    # ---- Wheel Zoom --------------------------------------------------------

    def _on_wheel_zoomed(self, delta: int):
        direction = 1 if delta > 0 else -1
        new_zoom = self._zoom_scale + direction * self._zoom_step
        new_zoom = round(new_zoom / self._zoom_step) * self._zoom_step
        new_zoom = max(self._zoom_min, min(self._zoom_max, new_zoom))

        if new_zoom != self._zoom_scale:
            self._zoom_scale = new_zoom
            if self._is_holding:
                self._render_frozen_display()
            else:
                self._update_preview()

    def _reset_zoom(self):
        self._zoom_scale = 1.0
        if self._is_holding:
            self._render_frozen_display()
        else:
            self._update_preview()

    def _show_about(self):
        dlg = _AboutDialog(self)
        dlg.exec_()

    def _on_scrub_zoomed(self, new_zoom: float):
        new_zoom = max(self._zoom_min, min(self._zoom_max, new_zoom))
        # 3% threshold + light lerp to suppress frame-to-frame jitter
        if abs(new_zoom - self._zoom_scale) < 0.03:
            return
        self._zoom_scale = self._zoom_scale * 0.6 + new_zoom * 0.4
        if self._is_holding:
            self._render_frozen_display()
        else:
            self._fast_zoom_update()

    def _poll_pen_state(self):
        """Poll mouse/pen state for scrub zoom. Fallback for non-tablet mice."""
        # If the app-level event filter is already handling a pen scrub,
        # skip polling so the two systems don't fight each other.
        if self._ef_scrubbing:
            return

        preview = self._active_preview()
        if preview is None or not preview.isVisible():
            return

        pos = QCursor.pos()
        preview_global = preview.mapToGlobal(QPoint(0, 0))
        preview_rect = QRect(preview_global, preview.size())

        # Direct Windows API query for button state.
        # This works for mice and Windows-Ink-mapped pens, but Wintab
        # pens bypass the Windows mouse subsystem entirely.
        # Fallback: SPACE (0x20) — keyboard goes through a completely
        # different subsystem, so it is immune to Wintab interception.
        user32 = ctypes.windll.user32
        left = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
        right = bool(user32.GetAsyncKeyState(0x02) & 0x8000)
        mid = bool(user32.GetAsyncKeyState(0x04) & 0x8000)
        space = bool(user32.GetAsyncKeyState(0x20) & 0x8000)
        button_pressed = left or right or mid or space

        # Throttled logging: only log when state changes to avoid spam
        if not hasattr(self, '_poll_last_logged'):
            self._poll_last_logged = ""
        log_line = f"poll: L={int(left)} R={int(right)} M={int(mid)} SP={int(space)} pos=({pos.x()},{pos.y()}) in_preview={preview_rect.contains(pos)}"
        if log_line != self._poll_last_logged:
            logger.debug(log_line)
            self._poll_last_logged = log_line

        # Heartbeat: prove the timer is running even when state is idle
        if not hasattr(self, '_poll_heartbeat'):
            self._poll_heartbeat = 0
        self._poll_heartbeat += 1
        if self._poll_heartbeat % 60 == 0:  # ~1 second at 16ms interval
            logger.debug("poll heartbeat (timer alive)")

        # Poll Krita foreground color changes (~5fps to avoid excessive re-renders)
        if self._poll_heartbeat % 10 == 0 and self._current_tab == "color":
            try:
                c = self._get_current_color()
                last = getattr(self, '_poll_last_color', None)
                if last is None or c.red() != last.red() or c.green() != last.green() or c.blue() != last.blue():
                    self._poll_last_color = c
                    self._update_preview()
            except Exception:
                pass

        if self._poll_scrubbing:
            if button_pressed:
                delta_x = pos.x() - self._poll_start_x
                if abs(delta_x) < 5:
                    effective_delta = 0
                else:
                    effective_delta = delta_x - (5 if delta_x > 0 else -5)
                new_zoom = self._poll_start_zoom + effective_delta * 0.005
                new_zoom = max(ZOOM_MIN, min(ZOOM_MAX, new_zoom))
                logger.debug(f"poll scrub: dx={delta_x} zoom={new_zoom:.3f}")
                self._on_scrub_zoomed(new_zoom)
            else:
                logger.debug("poll scrub END")
                self._poll_scrubbing = False
        else:
            if button_pressed and preview_rect.contains(pos):
                logger.debug("poll scrub START")
                self._poll_scrubbing = True
                self._poll_start_x = pos.x()
                self._poll_start_zoom = self._zoom_scale
                self._poll_moved = False

    def eventFilter(self, watched, event):
        """Application-level event filter.

        Intercepts TabletEvent and MouseEvent *before* Krita's canvas gets
        them. If the cursor is inside the active preview, we handle scrub
        zoom and swallow the event so the canvas doesn't react.
        """
        et = event.type()
        if et not in (
            QEvent.TabletPress, QEvent.TabletMove, QEvent.TabletRelease,
            QEvent.MouseButtonPress, QEvent.MouseMove, QEvent.MouseButtonRelease,
        ):
            return False

        preview = self._active_preview()
        if preview is None or not preview.isVisible():
            return False

        # Only intercept events whose target is the preview widget itself.
        # If the user clicks a button (e.g. Hold) that sits below the preview,
        # the cursor may still be inside the preview rect, but the event target
        # is the button — we must NOT swallow it.
        if watched is not preview and not preview.isAncestorOf(watched):
            return False

        # Global position from the event
        if hasattr(event, 'globalPos'):
            global_pos = event.globalPos()
        elif hasattr(event, 'globalX') and hasattr(event, 'globalY'):
            global_pos = QPoint(event.globalX(), event.globalY())
        else:
            return False

        preview_global = preview.mapToGlobal(QPoint(0, 0))
        preview_rect = QRect(preview_global, preview.size())

        # If we're already scrubbing, keep going even if the cursor drifted
        # slightly outside the preview (common with pen input).
        if not preview_rect.contains(global_pos) and not self._ef_scrubbing:
            return False

        if et in (QEvent.TabletPress, QEvent.MouseButtonPress):
            # Accept any button — Wintab drivers sometimes map pen tip to
            # MidButton, and event.button() can even be NoButton.
            if et == QEvent.MouseButtonPress:
                if not (event.buttons() & (Qt.LeftButton | Qt.MidButton | Qt.RightButton)):
                    return False
            self._ef_scrubbing = True
            self._ef_start_x = global_pos.x()
            self._ef_start_zoom = self._zoom_scale
            self._ef_moved = False
            return True

        elif et in (QEvent.TabletMove, QEvent.MouseMove) and self._ef_scrubbing:
            delta_x = global_pos.x() - self._ef_start_x
            if abs(delta_x) > 5:
                self._ef_moved = True
                effective_delta = delta_x - (5 if delta_x > 0 else -5)
            else:
                effective_delta = 0
            new_zoom = self._ef_start_zoom + effective_delta * 0.005
            new_zoom = max(ZOOM_MIN, min(ZOOM_MAX, new_zoom))
            self._on_scrub_zoomed(new_zoom)
            return True

        elif et in (QEvent.TabletRelease, QEvent.MouseButtonRelease) and self._ef_scrubbing:
            self._ef_scrubbing = False
            return True

        return False

    def _fast_zoom_update(self):
        """Fast zoom-only redraw using cached pixmap. No effect re-computation."""
        if self._last_pixmap is None or self._last_pixmap.isNull():
            self._update_preview()
            return
        if self._base_image is None:
            return

        preview = self._active_preview()
        if preview is None:
            return

        img_w, img_h = self._base_image.width(), self._base_image.height()
        label_w = max(preview.width() - 4, 50)
        label_h = max(preview.height() - 4, 50)
        fit_scale = min(label_w / max(img_w, 1), label_h / max(img_h, 1))
        final_scale = fit_scale * self._zoom_scale
        target_w = int(img_w * final_scale)
        target_h = int(img_h * final_scale)
        target_w = min(max(target_w, 10), 2048)
        target_h = min(max(target_h, 10), 2048)

        scaled = self._last_pixmap.scaled(
            target_w, target_h,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        preview.setPixmap(scaled)
        self._zoom_label.setText(f"{int(self._zoom_scale * 100)} %")

    # ---- Resize ------------------------------------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._base_image is not None:
            if self._is_holding:
                self._render_frozen_display()
            else:
                self._resize_timer.start()

    # ---- Board controls ----------------------------------------------------

    def _get_current_color(self):
        """Return Krita foreground color as a plain QColor."""
        try:
            app = Krita.instance()
            if app is None:
                return QColor(255, 255, 255)
            window = app.activeWindow()
            if window is None:
                return QColor(255, 255, 255)
            view = window.activeView()
            if view is None:
                return QColor(255, 255, 255)
            managed = view.foregroundColor()
            display_color = managed.colorForCanvas(view.canvas())
            return QColor(
                display_color.red(),
                display_color.green(),
                display_color.blue(),
                255
            )
        except Exception:
            return QColor(255, 255, 255)

    def _set_current_color(self, color):
        """Set Krita foreground color from a plain QColor."""
        try:
            app = Krita.instance()
            target_view = None
            for window in app.windows():
                for view in window.views():
                    if view.canvas() is not None:
                        target_view = view
                        break
                if target_view is not None:
                    break
            if target_view is None:
                target_view = app.activeWindow().activeView()
            if target_view is None:
                return
            old = target_view.foregroundColor()
            # Skip if color is effectively the same
            old_r = int(old.components()[0] * 255)
            old_g = int(old.components()[1] * 255)
            old_b = int(old.components()[2] * 255)
            if (old_r, old_g, old_b) == (color.red(), color.green(), color.blue()):
                return
            # Use fromQColor to let Krita handle channel mapping correctly
            if hasattr(old, 'fromQColor') and callable(getattr(old, 'fromQColor')):
                managed = old.fromQColor(color)
                if managed is not None:
                    target_view.setForeGroundColor(managed)
                    return
            # Fallback: manual setComponents (may have channel order issues)
            managed = ManagedColor(old.colorModel(), old.colorDepth(), old.colorProfile())
            managed.setComponents([
                color.red() / 255.0,
                color.green() / 255.0,
                color.blue() / 255.0,
                1.0,
            ])
            target_view.setForeGroundColor(managed)
        except Exception:
            pass

    def _on_brush_size_changed(self, value: int):
        self._board.set_brush_size(value)

    def _on_clear_board(self):
        self._board.clear()

    # ---- Event filter ------------------------------------------------------

    def eventFilter(self, obj, event):
        etype = event.type()
        if obj is self._resize_bar:
            if etype == QEvent.Enter:
                self._resize_bar.setStyleSheet("background-color: #333333;")
                return False
            elif etype == QEvent.Leave:
                self._resize_bar.setStyleSheet("background-color: #555555;")
                return False
            elif etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._resize_dragging = True
                self._resize_start_x = event.globalPos().x()
                self._resize_start_width = self.width()
                return True
            elif etype == QEvent.MouseMove and self._resize_dragging:
                delta = event.globalPos().x() - self._resize_start_x
                new_width = max(280, self._resize_start_width + delta)
                self.setMinimumWidth(int(new_width))
                self.resize(int(new_width), self.height())
                return True
            elif etype == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self._resize_dragging = False
                return True
        elif obj is self._bottom_bar:
            if etype == QEvent.Enter:
                self._bottom_bar.setStyleSheet("background-color: #333333;")
                return False
            elif etype == QEvent.Leave:
                self._bottom_bar.setStyleSheet("background-color: #555555;")
                return False
            elif etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._resize_dragging = True
                self._resize_start_y = event.globalPos().y()
                self._resize_start_height = self.height()
                return True
            elif etype == QEvent.MouseMove and self._resize_dragging:
                delta = event.globalPos().y() - self._resize_start_y
                new_height = max(480, self._resize_start_height + delta)
                self.setMinimumHeight(int(new_height))
                self.resize(self.width(), int(new_height))
                return True
            elif etype == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self._resize_dragging = False
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        super().keyPressEvent(event)
