"""Shared Light/Dark tokens and application styles.

Palette and standalone control conventions adapted from:
https://github.com/dathp95/UDS-Analysis/tree/main/gui/themes
No runtime dependency on that repository.
"""

from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Colors:
    primary: str
    hover: str
    pressed: str
    window: str
    panel: str
    text: str
    muted: str
    border: str
    selection: str
    selected_text: str
    primary_text: str
    disabled: str
    disabled_text: str
    danger: str
    warning: str
    warning_background: str


LIGHT = Colors("#0078D4", "#2899F5", "#005A9E", "#FFFFFF", "#F6F8FA", "#202020",
               "#586574", "#D9DFE5", "#D6E9F8", "#102D43", "#FFFFFF", "#E5E9ED",
               "#626D78", "#B42332", "#805000", "#FFF3D6")
DARK = Colors("#4CC2FF", "#70D1FF", "#008ECC", "#202124", "#2B2B2B", "#F2F2F2",
              "#B0BAC5", "#484D53", "#4CC2FF", "#10212A", "#10212A", "#363A40",
              "#A3ABB5", "#FF9595", "#FFE2A6", "#49381C")


class ThemeManager(QObject):
    changed = Signal()

    def __init__(self, name="light", parent=None):
        super().__init__(parent)
        self.name = name
        self.colors = DARK if name == "dark" else LIGHT

    def apply(self, name):
        self.name = "dark" if name == "dark" else "light"
        self.colors = c = DARK if self.name == "dark" else LIGHT
        app = QApplication.instance()
        palette = QPalette()
        roles = {
            QPalette.ColorRole.Window: c.window, QPalette.ColorRole.WindowText: c.text,
            QPalette.ColorRole.Base: c.window, QPalette.ColorRole.AlternateBase: c.panel,
            QPalette.ColorRole.Text: c.text, QPalette.ColorRole.Button: c.panel,
            QPalette.ColorRole.ButtonText: c.text, QPalette.ColorRole.Highlight: c.selection,
            QPalette.ColorRole.HighlightedText: c.selected_text,
            QPalette.ColorRole.ToolTipBase: c.panel, QPalette.ColorRole.ToolTipText: c.text,
            QPalette.ColorRole.PlaceholderText: c.muted,
        }
        for role, color in roles.items():
            palette.setColor(role, QColor(color))
        for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
            palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(c.disabled_text))
        app.setPalette(palette)
        app.setStyleSheet(f"""
            QWidget {{ font-family: 'Segoe UI'; font-size: 10pt; color: {c.text}; }}
            QMainWindow, QDialog {{ background: {c.window}; }}
            QLabel[role='title'] {{ font-size: 22pt; font-weight: 600; }}
            QLabel[role='heading'] {{ font-size: 15pt; font-weight: 600; }}
            QLabel[role='muted'] {{ color: {c.muted}; }}
            QLabel[role='error'] {{ color: {c.danger}; padding: 5px 0; }}
            QLabel[role='badge'] {{ color: {c.primary}; background: {c.panel}; border: 1px solid {c.border}; border-radius: 6px; padding: 7px 12px; }}
            QPushButton {{ background: {c.panel}; border: 1px solid {c.border}; border-radius: 6px; padding: 6px 16px; }}
            QPushButton:hover {{ border-color: {c.primary}; background: {c.selection}; color: {c.selected_text}; }}
            QPushButton:pressed {{ border-color: {c.primary}; }}
            QPushButton[variant='primary'] {{ background: {c.primary}; color: {c.primary_text}; border-color: {c.primary}; font-weight: 600; }}
            QPushButton[variant='primary']:hover {{ background: {c.hover}; }}
            QPushButton[variant='primary']:pressed {{ background: {c.pressed}; }}
            QPushButton:disabled {{ background: {c.disabled}; color: {c.disabled_text}; border-color: {c.border}; }}
            QLineEdit, QComboBox, QSpinBox {{ background: {c.window}; border: 1px solid {c.border}; border-radius: 5px; padding: 7px; min-height: 20px; }}
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {c.primary}; }}
            QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ background: {c.disabled}; color: {c.disabled_text}; }}
            QComboBox QAbstractItemView {{ background: {c.window}; selection-background-color: {c.selection}; selection-color: {c.selected_text}; }}
            QTabWidget::pane {{ border: 1px solid {c.border}; border-radius: 6px; }}
            QTabBar::tab {{ background: {c.window}; color: {c.muted}; padding: 12px 24px; border-bottom: 3px solid transparent; }}
            QTabBar::tab:selected {{ color: {c.primary}; border-bottom-color: {c.primary}; }}
            QTabBar::tab:hover {{ background: {c.panel}; }}
            QTableWidget {{ background: {c.window}; alternate-background-color: {c.panel}; gridline-color: {c.border}; border: 1px solid {c.border}; border-radius: 4px; selection-background-color: {c.selection}; selection-color: {c.selected_text}; }}
            QTableWidget::item {{ padding: 6px; }}
            QTableWidget::item:selected {{ background: {c.selection}; color: {c.selected_text}; }}
            QHeaderView::section {{ background: {c.panel}; color: {c.muted}; border: 0; border-bottom: 1px solid {c.border}; padding: 10px 6px; font-weight: 600; }}
            QPlainTextEdit {{ background: {c.panel}; border: 1px solid {c.border}; border-radius: 5px; padding: 9px; selection-background-color: {c.selection}; selection-color: {c.selected_text}; }}
            QListWidget {{ background: {c.panel}; border: 1px solid {c.border}; border-radius: 5px; padding: 6px; }}
            QListWidget::item {{ padding: 4px; }}
            QScrollArea {{ border: 0; background: {c.window}; }}
            QCheckBox {{ spacing: 6px; }}
            QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {c.border}; border-radius: 3px; background: {c.window}; }}
            QCheckBox::indicator:checked {{ background: {c.primary}; border-color: {c.primary}; }}
            QCheckBox::indicator:disabled {{ background: {c.disabled}; }}
            QGroupBox {{ border: 1px solid {c.border}; border-radius: 6px; margin-top: 18px; padding: 18px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 5px; color: {c.muted}; }}
            QToolTip {{ background: {c.panel}; color: {c.text}; border: 1px solid {c.border}; }}
        """)
        self.changed.emit()
