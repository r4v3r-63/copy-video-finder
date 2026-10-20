"""Плавная прокрутка колесиком мыши: вместо рывка на строку — короткая анимация на несколько десятков точек."""

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation
from PyQt6.QtWidgets import QAbstractItemView, QAbstractScrollArea

DURATION = 220  # мс
STEP = 120  # точек за один щелчок колесика


class SmoothScroller(QObject):
    """Перехватывает колесико над областью прокрутки и плавно сдвигает вертикальную полосу.

    Сенсорная панель присылает точные смещения (pixelDelta) — их не трогаем, они и так плавные.
    """

    def __init__(self, area: QAbstractScrollArea, step: int = STEP, duration: int = DURATION):
        super().__init__(area)
        self.area, self.step = area, step
        if isinstance(area, QAbstractItemView):
            area.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        bar = area.verticalScrollBar()
        bar.setSingleStep(max(step // 4, 10))
        self.target = bar.value()
        self.animation = QPropertyAnimation(bar, b"value", self)
        self.animation.setDuration(duration)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        area.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.Wheel or not event.pixelDelta().isNull():
            return False
        delta = event.angleDelta().y()
        if not delta or event.modifiers():  # Ctrl/Shift+колесико — как обычно
            return False
        bar = self.area.verticalScrollBar()
        running = self.animation.state() == QPropertyAnimation.State.Running
        start = self.target if running else bar.value()  # быстрые щелчки подряд складываются
        self.target = max(bar.minimum(), min(bar.maximum(), start - round(delta / 120 * self.step)))
        self.animation.stop()
        self.animation.setStartValue(bar.value())
        self.animation.setEndValue(self.target)
        self.animation.start()
        return True


def smooth(area: QAbstractScrollArea, step: int = STEP) -> SmoothScroller:
    return SmoothScroller(area, step)
