"""Explanation popup for a single biq action (a bid or a card play).

Shown when the user clicks a bid in the auction or a card biq has played,
provided the "Explain biq's actions" preference is on. The content is built
by backend.explain.Explanation; this dialog is just the rendering shell and
follows the shared dialog styling.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
)

from .dialog_style import apply_dialog_style


class ExplanationDialog(QDialog):
    """Render a backend.explain.Explanation as a tidy why-popup."""

    def __init__(self, explanation, parent=None):
        super().__init__(parent)
        self.explanation = explanation
        self.setWindowTitle("Why biq did that")
        self.setMinimumWidth(420)
        apply_dialog_style(self)
        self._setup_ui()

    def _setup_ui(self):
        ex = self.explanation
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        # Title — the action itself.
        title = QLabel(ex.title or "biq's action")
        title.setFont(QFont("Sans Serif", 15, QFont.Weight.Bold))
        title.setWordWrap(True)
        layout.addWidget(title)

        # Subtitle — system / contract context.
        if ex.subtitle:
            sub = QLabel(ex.subtitle)
            sub.setFont(QFont("Sans Serif", 10))
            sub.setStyleSheet("color: #555;")
            sub.setWordWrap(True)
            layout.addWidget(sub)

        # Separator.
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        # Headline — the one-line "why".
        if ex.headline:
            head = QLabel(ex.headline)
            head.setFont(QFont("Sans Serif", 12, QFont.Weight.Bold))
            head.setWordWrap(True)
            layout.addWidget(head)

        # Supporting detail bullets.
        for point in (ex.points or []):
            bullet = QLabel(f"•  {point}")
            bullet.setFont(QFont("Sans Serif", 10))
            bullet.setWordWrap(True)
            bullet.setContentsMargins(6, 0, 0, 0)
            layout.addWidget(bullet)

        # Citation chip — the convention / technique that applies.
        if ex.citation:
            cite = QLabel(f"Convention / technique: {ex.citation}")
            cite.setFont(QFont("Sans Serif", 10, QFont.Weight.Bold))
            cite.setStyleSheet(
                "color: #14401e; background-color: #e3efe6;"
                " border: 1px solid #b9d4c1; border-radius: 4px;"
                " padding: 4px 8px;")
            cite.setWordWrap(True)
            layout.addWidget(cite)

        # Engine note — which engine produced it (honesty footer).
        if ex.note:
            note = QLabel(ex.note)
            note.setFont(QFont("Sans Serif", 9))
            note.setStyleSheet("color: #777; font-style: italic;")
            note.setWordWrap(True)
            layout.addWidget(note)

        layout.addStretch()

        # Close button.
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_btn.setDefault(True)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
