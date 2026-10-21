"""Карточки выдачи: превью, название, площадка и статус лицензии."""

from PyQt6.QtCore import QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate

from app.core import design
from app.storage import catalog as cat
from app.ui import theme
from app.ui.widgets.player import format_duration


def card_notes(result, status: str | None, off_topic: bool = False) -> str:
    notes = []
    if status == cat.SELECTED:
        notes.append("В каталоге")
    elif status == cat.REJECTED:
        notes.append("Отклонён ранее")
    if off_topic:
        notes.append("Не по теме")
    elif result.match_hint and result.match_hint != cat.EXACT:
        notes.append(result.match_hint)
    if not result.files:
        notes.append("Смотреть на сайте")
    elif result.width and result.height:
        notes.append(f"{result.width}×{result.height}")
    return " · ".join(notes)


class ResultCardDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(design.RESULT_WIDTH, design.RESULT_HEIGHT)

    def paint(self, painter: QPainter, option, index):
        result = index.data(Qt.ItemDataRole.UserRole)
        if result is None:
            super().paint(painter, option, index)
            return
        p = theme.palette()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        card = option.rect.adjusted(design.SPACE_XS, design.SPACE_XS, -design.SPACE_XS, -design.SPACE_XS)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        background = p.selection_bg if selected else p.selection_hover if hover else p.bg_surface
        painter.setBrush(QColor(background))
        painter.setPen(QPen(QColor(p.accent_primary if selected else p.border),
                            design.FOCUS_WIDTH if selected else design.BORDER_WIDTH))
        painter.drawRoundedRect(QRectF(card), design.RADIUS_BUTTON, design.RADIUS_BUTTON)
        content = card.adjusted(design.RESULT_PADDING, design.RESULT_PADDING,
                                -design.RESULT_PADDING, -design.RESULT_PADDING)
        thumb = QRect(content.x(), content.y(), content.width(), design.THUMB_HEIGHT)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(thumb), design.PREVIEW_RADIUS, design.PREVIEW_RADIUS)
        painter.save()
        painter.setClipPath(clip)
        painter.fillRect(thumb, QColor(p.video_bg))
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if icon is not None and not icon.isNull():
            image = icon.pixmap(QSize(design.THUMB_WIDTH, design.THUMB_HEIGHT)).scaled(
                thumb.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(thumb.center() - image.rect().center(), image)
        else:
            painter.setPen(QColor(p.text_secondary))
            painter.drawText(thumb, Qt.AlignmentFlag.AlignCenter,
                             index.data(Qt.ItemDataRole.UserRole + 4) or "Без превью")
        painter.restore()
        caption_font = QFont(option.font)
        caption_font.setPixelSize(design.FONT_CAPTION)
        painter.setFont(caption_font)
        if result.duration is not None:
            duration = format_duration(result.duration)
            width = painter.fontMetrics().horizontalAdvance(duration) + 2 * design.SPACE_XS
            overlay = QRect(thumb.right() - width - design.SPACE_XS,
                            thumb.bottom() - design.BADGE_HEIGHT - design.SPACE_XS, width, design.BADGE_HEIGHT)
            color = QColor(p.video_bg)
            color.setAlpha(design.OVERLAY_OPACITY)
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(overlay), design.SPACE_XS, design.SPACE_XS)
            painter.setPen(QColor(design.WHITE))
            painter.drawText(overlay, Qt.AlignmentFlag.AlignCenter, duration)
        title = QRect(content.x(), thumb.bottom() + design.SPACE_XS, content.width(), design.RESULT_TITLE_HEIGHT)
        title_font = QFont(option.font)
        title_font.setPixelSize(design.FONT_BODY)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor(p.text_primary))
        painter.drawText(title, Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(result.title or "Без названия",
                                                         Qt.TextElideMode.ElideRight, title.width()))
        painter.setFont(caption_font)
        label, tone = theme.license_label(result.license)
        badge_width = painter.fontMetrics().horizontalAdvance(label) + 2 * design.BADGE_PADDING
        row = QRect(content.x(), title.bottom() + design.SPACE_XS, content.width(), design.RESULT_META_HEIGHT)
        badge = QRect(row.right() - badge_width, row.y(), badge_width, design.BADGE_HEIGHT)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(p.badge_bg[tone]))
        painter.drawRoundedRect(QRectF(badge), design.SPACE_XS, design.SPACE_XS)
        painter.setPen(QColor(p.badge_text[tone]))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)
        painter.setPen(QColor(p.text_secondary))
        source = QRect(row.x(), row.y(), max(0, badge.x() - row.x() - design.SPACE_XS), row.height())
        painter.drawText(source, Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(result.source, Qt.TextElideMode.ElideRight, source.width()))
        # Дополнительные отметки остаются видны, хотя основная подпись теперь — название ролика.
        notes = index.data(Qt.ItemDataRole.UserRole + 2) or ""
        footer = QRect(content.x(), row.bottom() + design.SPACE_XS, content.width(), design.FONT_SUBTITLE)
        painter.drawText(footer, Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(notes, Qt.TextElideMode.ElideRight, footer.width()))
        painter.restore()
