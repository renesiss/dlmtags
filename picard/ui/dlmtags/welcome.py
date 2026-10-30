# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""DLMtags startup safety notice."""

from PyQt6 import (
    QtCore,
    QtGui,
    QtSvg,
    QtWidgets,
)

from picard.i18n import gettext as _

import picard.resources  # noqa: F401  # Ensure the bundled logo resource is registered.

from picard.ui.dlmtags.components.styles import style_component


class DLMtagsWelcomeDialog(QtWidgets.QDialog):
    """Localized startup safety notice using Picard's existing preference."""

    _shown_this_session = False

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('dlmtagsWelcomeDialog')
        self.setWindowTitle(_('Protect your music library'))
        self.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        self.setMinimumWidth(620)
        self.setSizeGripEnabled(False)
        style_component(self, 'welcomeDialog')

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(14)

        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(18)
        logo = QtWidgets.QLabel(self)
        logo.setObjectName('dlmtagsWelcomeLogo')
        source_logo = QtSvg.QSvgRenderer(':/images/dlmtags-logo.svg')
        view_box = source_logo.viewBoxF()
        logo_width = 120
        logo_height = round(logo_width * view_box.height() / view_box.width())
        logo_pixmap = QtGui.QPixmap(logo_width, logo_height)
        logo_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(logo_pixmap)
        source_logo.render(painter, QtCore.QRectF(0, 0, logo_width, logo_height))
        painter.end()
        logo.setPixmap(logo_pixmap)
        logo.setFixedSize(logo_width, logo_height)
        logo.setAccessibleName(_('DLMtags logo'))
        header.addWidget(logo, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)

        self.title_label = QtWidgets.QLabel(_('Protect your music library'), self)
        self.title_label.setObjectName('dlmtagsWelcomeTitle')
        self.title_label.setWordWrap(True)
        header.addWidget(self.title_label, 1, QtCore.Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        self.intro_label = QtWidgets.QLabel(
            _(
                'Saving changes in DLMtags can overwrite existing metadata and, depending on your settings, '
                'modify filenames.'
            ),
            self,
        )
        self.intro_label.setObjectName('dlmtagsWelcomeIntro')
        self.intro_label.setWordWrap(True)
        layout.addWidget(self.intro_label)

        recommendation_heading = QtWidgets.QLabel(_('We recommend:'), self)
        recommendation_heading.setObjectName('dlmtagsWelcomeRecommendationHeading')
        layout.addWidget(recommendation_heading)

        recommendations = QtWidgets.QVBoxLayout()
        recommendations.setContentsMargins(8, 0, 0, 0)
        recommendations.setSpacing(6)
        self.recommendation_labels = []
        for index, text in enumerate(
            (
                _('Backing up your music before making bulk changes.'),
                _('Reviewing proposed tags before confirming.'),
                _('Testing with a small number of copied files first.'),
            )
        ):
            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)
            bullet = QtWidgets.QLabel('•', self)
            bullet.setProperty('dlmtagsWelcomeBullet', True)
            bullet.setAccessibleName(_('Recommendation %d') % (index + 1))
            item = QtWidgets.QLabel(text, self)
            item.setWordWrap(True)
            self.recommendation_labels.append(item)
            row.addWidget(bullet, 0, QtCore.Qt.AlignmentFlag.AlignTop)
            row.addWidget(item, 1)
            recommendations.addLayout(row)
        layout.addLayout(recommendations)

        self.license_note = QtWidgets.QLabel(
            _('DLMtags is free, open-source software based on MusicBrainz Picard and is provided without warranty.'),
            self,
        )
        self.license_note.setObjectName('dlmtagsWelcomeLicense')
        self.license_note.setWordWrap(True)
        layout.addWidget(self.license_note)

        footer = QtWidgets.QHBoxLayout()
        footer.setContentsMargins(0, 4, 0, 0)
        self.dont_show_again = QtWidgets.QCheckBox(_("Don't show again"), self)
        self.dont_show_again.setObjectName('dlmtagsWelcomeDontShowAgain')
        footer.addWidget(self.dont_show_again, 1)
        self.continue_button = QtWidgets.QPushButton(_('Continue'), self)
        self.continue_button.setObjectName('dlmtagsWelcomeContinue')
        self.continue_button.setDefault(True)
        self.continue_button.clicked.connect(self.accept)
        footer.addWidget(self.continue_button)
        layout.addLayout(footer)

        self.adjustSize()
        self.resize(620, self.sizeHint().height())

    def show_startup_warning(self):
        """Show once per process; return whether the preference stays enabled."""
        cls = type(self)
        if cls._shown_this_session:
            return None
        cls._shown_this_session = True
        self.exec()
        return not self.dont_show_again.isChecked()
