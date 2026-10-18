"""Small native Qt bar chart using the shared palette; no browser/chart dependency."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget
from services.revenue_service import format_vnd


class ReceiptChart(QWidget):
    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme, self.periods = theme, ()
        self.setMinimumHeight(170)
        self.setMouseTracking(True)
        self.setAccessibleName('Confirmed receipts by local calendar period')
        theme.changed.connect(self.update)

    def set_periods(self, periods):
        self.periods = periods
        self.setAccessibleDescription(f'{len(periods)} periods; confirmed receipts, not profit.')
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = self.theme.colors
        painter.fillRect(self.rect(), QColor(c.panel))
        painter.setPen(QColor(c.muted))
        maximum = max((p.total_vnd for p in self.periods), default=0)
        painter.drawText(12, 22, format_vnd(maximum))
        left, right, top, bottom = 15, self.width() - 15, 35, self.height() - 35
        painter.setPen(QColor(c.border))
        painter.drawLine(left, bottom, right, bottom)
        if not maximum:
            painter.setPen(QColor(c.muted))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'No confirmed receipts in this range' if self.periods else 'Waiting for report…')
        if not self.periods:
            return
        width = (right - left) / len(self.periods)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(c.primary))
        for index, period in enumerate(self.periods):
            height = (bottom - top) * period.total_vnd / maximum if maximum else 0
            painter.drawRect(QRectF(left + index * width + width * .12, bottom - height, max(.5, width * .76), height))
        painter.setPen(QColor(c.muted))
        stride = max(1, (len(self.periods) + 7) // 8)
        for index in range(0, len(self.periods), stride):
            period = self.periods[index]
            label = period.start.strftime('%d/%m/%y')
            x = max(left, min(left + index * width - 18, right - 75))
            painter.drawText(QRectF(x, bottom + 8, 75, 24), Qt.AlignmentFlag.AlignLeft, label)

    def mouseMoveEvent(self, event):
        width = self.width() - 30
        if self.periods and width > 0:
            index = int((event.position().x() - 15) * len(self.periods) / width)
            if 0 <= index < len(self.periods):
                period = self.periods[index]
                self.setToolTip(f'{period.label}\n{format_vnd(period.total_vnd)} · {period.paid_orders} paid orders')
                return
        self.setToolTip('')
