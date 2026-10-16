"""Small standalone controls adapted from the UDS-Analysis reference conventions."""

from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QPushButton, QTableWidget


class Button(QPushButton):
    def __init__(self, text, *, primary=False, parent=None):
        super().__init__(text, parent)
        self.setMinimumHeight(36)
        self.setProperty("variant", "primary" if primary else "secondary")


class RecordTable(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(38)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setDefaultSectionSize(165)
        header.setStretchLastSection(True)
        header.setSortIndicatorShown(True)
