# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""DLMtags settings for optional API keys and visible metadata fields."""

import os.path
from typing import ClassVar

from PyQt6 import (
    QtCore,
    QtGui,
    QtWidgets,
)

from picard.config import get_config
from picard.extension_points.options_pages import register_options_page
from picard.i18n import (
    N_,
    gettext as _,
)

from picard.ui.dlmtags.api_credentials import (
    API_PROVIDERS,
    ApiCredentialTester,
)
from picard.ui.dlmtags.components.action_button import ActionButton
from picard.ui.dlmtags.components.styles import style_component
from picard.ui.dlmtags.tag_editor import PRIMARY_FIELDS
from picard.ui.options import (
    OptionsPage,
    PageOptionConfigs,
)
from picard.ui.options.interface import (
    InterfaceOptionsPage,
    ReadTheDocs,
    changes_require_restart_warning,
)


API_KEYS = (
    ('dlmtags_discogs_token', _('Discogs Personal Access Token')),
    ('lastfm_api_key', _('Last.fm API Key')),
)
DEFAULT_VISIBLE_TAGS = ('title', 'artist', 'date', 'genre')
DLMTAGS_SETTINGS_PAGE_NAMES = frozenset(
    {'dlmtags_general', 'dlmtags_api_keys', 'dlmtags_visible_tags', 'dlmtags_file_settings'}
)
VISIBLE_TAGS = tuple((tag, _(label)) for tag, label in PRIMARY_FIELDS if not tag.startswith('~'))
FILE_SETTINGS = (
    ('enable_tag_saving', _('Save tags to files')),
    ('preserve_timestamps', _('Preserve file timestamps')),
    ('preserve_images', _('Keep embedded images')),
    ('remove_ape_from_mp3', _('Remove APEv2 tags from MP3 files')),
    ('remove_id3_from_flac', _('Remove ID3 tags from FLAC files')),
    ('fix_missing_seekpoints_flac', _('Fix missing seekpoints in FLAC files')),
)
SETTINGS_NAV_ITEMS = (
    ('dlmtags_general', N_('General'), 'settings.svg'),
    ('dlmtags_api_keys', N_('API Keys'), 'api-keys.svg'),
    ('dlmtags_visible_tags', N_('Visible Tags'), 'visible-tags.svg'),
    ('dlmtags_file_settings', N_('Files'), 'file-settings.svg'),
)


class DLMtagsSettingsNavigation(QtWidgets.QWidget):
    """Compact icon navigation for the curated DLMtags settings pages."""

    page_selected = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('dlmtagsSettingsNavigation')
        self.setMinimumWidth(170)
        self.buttons = {}
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        for page_name, label, icon_name in SETTINGS_NAV_ITEMS:
            action = QtGui.QAction(_(label), self)
            action.setCheckable(True)
            icon_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'dlmtags', 'icons', icon_name))
            action.setIcon(QtGui.QIcon(icon_path))
            button = ActionButton(action, self)
            button.setObjectName(f'dlmtags_settings_nav_{page_name}')
            button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setIconSize(QtCore.QSize(24, 24))
            button.setMinimumHeight(40)
            button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
            button.setProperty('dlmtagsSettingsNav', True)
            style_component(button, 'actionButton')
            action.triggered.connect(lambda checked=False, name=page_name: self.page_selected.emit(name))
            self.buttons[page_name] = button
            layout.addWidget(button)
        layout.addStretch(1)

    def set_current_page(self, page_name):
        for name, button in self.buttons.items():
            action = button.defaultAction()
            if action is not None:
                with QtCore.QSignalBlocker(action):
                    action.setChecked(name == page_name)


def connect_api_key_sync(widget, key):
    """Keep duplicate API-key fields in this Settings dialog synchronized."""
    object_name = f'dlmtags_api_key_{key}'
    widget.setObjectName(object_name)

    def sync(text):
        for other in widget.window().findChildren(QtWidgets.QLineEdit, object_name):
            if other is not widget and other.text() != text:
                with QtCore.QSignalBlocker(other):
                    other.setText(text)

    widget.textChanged.connect(sync)


def load_api_key(widget, key, value):
    """Load persisted credentials unless another visited page has pending edits."""
    object_name = f'dlmtags_api_key_{key}'
    for other in widget.window().findChildren(QtWidgets.QLineEdit, object_name):
        if other is not widget and other.property('dlmtagsApiKeyLoaded'):
            value = other.text()
            break
    with QtCore.QSignalBlocker(widget):
        widget.setText(value)
    widget.setProperty('dlmtagsApiKeyLoaded', True)


def _dlmtags_icon(name):
    """Load a bundled DLMtags SVG independently of the working directory."""
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'dlmtags', 'icons', name))
    return QtGui.QIcon(path)


class DLMtagsGeneralOptionsPage(InterfaceOptionsPage):
    NAME = 'dlmtags_general'
    TITLE = N_('General')
    PARENT = None
    SORT_ORDER = 1
    OPTIONS: ClassVar[PageOptionConfigs] = {'ui_language': {}, 'ui_theme': {}}

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.ui.groupBox.setTitle(_('General'))
        self.ui.toolbar_show_labels.hide()
        self.ui.show_menu_icons.hide()
        self.ui.miscellaneous_box.hide()

    def save(self):
        config = get_config()
        new_theme = str(self.ui.ui_theme.itemData(self.ui.ui_theme.currentIndex()))
        new_language = self.ui.ui_language.itemData(self.ui.ui_language.currentIndex())
        warnings = []
        if new_theme != config.setting['ui_theme']:
            config.setting['ui_theme'] = new_theme
        if new_language != config.setting['ui_language']:
            config.setting['ui_language'] = new_language
            warnings.append(_('You have changed the interface language.'))
            ReadTheDocs.update_documentation_items()
        changes_require_restart_warning(self, warnings=warnings, notes=[])


class DLMtagsFileSettingsOptionsPage(OptionsPage):
    NAME = 'dlmtags_file_settings'
    TITLE = N_('File Settings')
    PARENT = None
    SORT_ORDER = 77
    ACTIVE = True
    OPTIONS: ClassVar[PageOptionConfigs] = {key: {'widgets': [f'dlmtags_file_{key}']} for key, label in FILE_SETTINGS}

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        layout = QtWidgets.QVBoxLayout(self)
        self.checks = {}
        for key, label in FILE_SETTINGS:
            check = QtWidgets.QCheckBox(label, self)
            check.setObjectName(f'dlmtags_file_{key}')
            self.checks[key] = check
            layout.addWidget(check)
        layout.addStretch(1)

    def load(self):
        config = get_config()
        for key, check in self.checks.items():
            check.setChecked(config.setting[key])

    def save(self):
        config = get_config()
        for key, check in self.checks.items():
            config.setting[key] = check.isChecked()


class DLMtagsApiKeysOptionsPage(OptionsPage):
    NAME = 'dlmtags_api_keys'
    TITLE = N_('API Keys')
    PARENT = None
    SORT_ORDER = 75
    ACTIVE = True

    OPTIONS: ClassVar[PageOptionConfigs] = {
        'dlmtags_discogs_enabled': {'widgets': ['dlmtags_discogs_enabled']},
        'smart_genre_lastfm_enabled': {'widgets': ['smart_genre_lastfm_enabled']},
    }

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName('dlmtagsApiKeysPage')
        style_component(self, 'apiKeysPage')
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        self.credential_tester = ApiCredentialTester(self)
        self.credential_tester.finished.connect(self._credential_test_finished)
        self.api_key_widgets = {}
        self.enable_widgets = {}
        self.edit_buttons = {}
        self.connect_buttons = {}
        self.test_buttons = {}
        self.status_labels = {}
        key_providers = {'dlmtags_discogs_token': 'discogs', 'lastfm_api_key': 'lastfm'}

        header = QtWidgets.QWidget(self)
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 2)
        header_layout.setSpacing(12)
        key_icon = QtWidgets.QLabel(header)
        key_icon.setPixmap(_dlmtags_icon('api-keys.svg').pixmap(36, 36))
        key_icon.setFixedSize(36, 36)
        key_icon.setAccessibleName(_('API Keys icon'))
        header_layout.addWidget(key_icon)
        heading = QtWidgets.QVBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(2)
        title = QtWidgets.QLabel(_('API Keys'), header)
        title.setProperty('dlmtagsApiTitle', True)
        description = QtWidgets.QLabel(
            _('Manage optional Discogs and Last.fm credentials for metadata suggestions.'),
            header,
        )
        description.setProperty('dlmtagsApiDescription', True)
        description.setWordWrap(True)
        heading.addWidget(title)
        heading.addWidget(description)
        header_layout.addLayout(heading, 1)
        layout.addWidget(header)

        page_separator = QtWidgets.QFrame(self)
        page_separator.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        page_separator.setFrameShadow(QtWidgets.QFrame.Shadow.Plain)
        page_separator.setProperty('dlmtagsComponent', 'separator')
        layout.addWidget(page_separator)

        for key, label in API_KEYS:
            provider = key_providers[key]
            details = API_PROVIDERS[provider]
            group = QtWidgets.QFrame(self)
            group.setProperty('dlmtagsApiCard', True)
            card_layout = QtWidgets.QVBoxLayout(group)
            card_layout.setContentsMargins(14, 10, 14, 10)
            card_layout.setSpacing(8)

            provider_header = QtWidgets.QHBoxLayout()
            provider_header.setContentsMargins(0, 0, 0, 0)
            provider_name = QtWidgets.QLabel(details['title'], group)
            provider_name.setProperty('dlmtagsApiProvider', True)
            provider_header.addWidget(provider_name)
            provider_header.addStretch(1)
            status = QtWidgets.QLabel(_('Not Tested'), group)
            status.setObjectName(f'dlmtags_api_status_{provider}')
            status.setProperty('dlmtagsApiStatus', True)
            status.setProperty('apiCredentialStatus', 'not-tested')
            status.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            status.setToolTip(_('Test this credential to verify it with the provider.'))
            self.status_labels[provider] = status
            provider_header.addWidget(status)
            card_layout.addLayout(provider_header)

            card_separator = QtWidgets.QFrame(group)
            card_separator.setFrameShape(QtWidgets.QFrame.Shape.HLine)
            card_separator.setFrameShadow(QtWidgets.QFrame.Shadow.Plain)
            card_separator.setProperty('dlmtagsComponent', 'separator')
            card_layout.addWidget(card_separator)

            enabled = QtWidgets.QCheckBox(_('Use for metadata suggestions'), group)
            enabled.setObjectName(details['enabled_key'])
            enabled.setToolTip(_('Allow this provider to contribute optional metadata suggestions.'))

            edit = QtWidgets.QLineEdit(group)
            edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
            edit.setAccessibleName(label)
            edit.setPlaceholderText(label)
            edit.setProperty('dlmtagsApiCredential', True)
            edit.setReadOnly(True)
            connect_api_key_sync(edit, key)
            self.api_key_widgets[key] = edit
            card_layout.addWidget(edit)

            controls = QtWidgets.QHBoxLayout()
            controls.setContentsMargins(0, 0, 0, 0)
            controls.setSpacing(8)
            controls.addWidget(enabled)
            controls.addStretch(1)
            edit_button = QtWidgets.QPushButton(_('Edit'), group)
            edit_button.setIcon(_dlmtags_icon('api-edit.svg'))
            edit_button.setIconSize(QtCore.QSize(16, 16))
            edit_button.setProperty('dlmtagsApiAction', True)
            edit_button.setCheckable(True)
            edit_button.setToolTip(_('Allow editing this saved key; changes apply when you Save Settings.'))
            edit_button.toggled.connect(lambda editing, field=edit: self._set_key_editable(field, editing))
            self.edit_buttons[provider] = edit_button
            connect_button = QtWidgets.QPushButton(_('Connect'), group)
            connect_button.setIcon(_dlmtags_icon('api-connect.svg'))
            connect_button.setIconSize(QtCore.QSize(16, 16))
            connect_button.setProperty('dlmtagsApiAction', True)
            connect_button.setToolTip(
                _('Open the provider page to create or manage an API key. These providers use API keys, not OAuth.')
            )
            connect_button.clicked.connect(
                lambda checked=False, url=details['connect_url']: QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))
            )
            self.connect_buttons[provider] = connect_button
            test_button = QtWidgets.QPushButton(_('Test'), group)
            test_button.setIcon(_dlmtags_icon('api-test.svg'))
            test_button.setIconSize(QtCore.QSize(16, 16))
            test_button.setProperty('dlmtagsApiAction', True)
            test_button.setToolTip(_('Send an asynchronous request to verify this credential.'))
            test_button.clicked.connect(lambda checked=False, name=provider: self._test_provider(name))
            self.test_buttons[provider] = test_button
            controls.addWidget(edit_button)
            controls.addWidget(connect_button)
            controls.addWidget(test_button)
            card_layout.addLayout(controls)
            self.enable_widgets[provider] = enabled
            enabled.toggled.connect(lambda state, name=provider: self._update_test_enabled(name))
            edit.textChanged.connect(lambda value, name=provider: self._credential_changed(name))
            layout.addWidget(group)
        layout.addStretch(1)

    def load(self):
        config = get_config()
        for key, edit in self.api_key_widgets.items():
            load_api_key(edit, key, config.setting[key])
        for provider, enabled in self.enable_widgets.items():
            with QtCore.QSignalBlocker(enabled):
                enabled.setChecked(config.setting[API_PROVIDERS[provider]['enabled_key']])
            self.edit_buttons[provider].setChecked(False)
            self._set_status(provider, 'not-tested')
            self._update_test_enabled(provider)

    def save(self):
        config = get_config()
        for key, edit in self.api_key_widgets.items():
            config.setting[key] = edit.text().strip()
        for provider, enabled in self.enable_widgets.items():
            config.setting[API_PROVIDERS[provider]['enabled_key']] = enabled.isChecked()

    def _set_key_editable(self, edit, editable):
        edit.setReadOnly(not editable)
        if editable:
            edit.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)

    def _credential_changed(self, provider):
        self._set_status(provider, 'not-tested')
        self._update_test_enabled(provider)

    def _update_test_enabled(self, provider):
        credential_key = API_PROVIDERS[provider]['credential_key']
        self.test_buttons[provider].setEnabled(bool(self.api_key_widgets[credential_key].text().strip()))

    def _set_status(self, provider, state, message=None):
        label = self.status_labels[provider]
        label.setProperty('apiCredentialStatus', state)
        if state == 'not-tested':
            text = _('Not Tested')
        elif state == 'testing':
            text = _('Testing…')
        elif state == 'connected':
            text = _('Connected')
        else:
            text = _('Failed')
        label.setText(text)
        label.setToolTip(message or '')
        style = label.style()
        if style is not None:
            style.unpolish(label)
            style.polish(label)

    def _test_provider(self, provider):
        key = API_PROVIDERS[provider]['credential_key']
        credential = self.api_key_widgets[key].text().strip()
        if not credential:
            self._set_status(provider, 'failed', _('Enter a credential before testing.'))
            return
        self.test_buttons[provider].setEnabled(False)
        self._set_status(provider, 'testing')
        self.credential_tester.test(provider, credential)

    def _credential_test_finished(self, provider, success, message):
        self.test_buttons[provider].setEnabled(False)
        self._set_status(provider, 'connected' if success else 'failed', message)
        self._update_test_enabled(provider)


class VisibleTagsOptionsPage(OptionsPage):
    NAME = 'dlmtags_visible_tags'
    TITLE = N_('Visible Tags')
    PARENT = None
    SORT_ORDER = 76
    ACTIVE = True
    OPTIONS: ClassVar[PageOptionConfigs] = {'dlmtags_visible_tags': {}}

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(
            QtWidgets.QLabel(
                _('Choose which metadata fields appear in Original Tags and New Tags. Hidden values remain unchanged.'),
                self,
            )
        )
        self.checks = {}
        for tag, label in VISIBLE_TAGS:
            check = QtWidgets.QCheckBox(label, self)
            self.checks[tag] = check
            layout.addWidget(check)
        restore = QtWidgets.QPushButton(_('Restore Defaults'), self)
        restore.clicked.connect(self._restore_visible_defaults)
        layout.addWidget(restore)
        layout.addStretch(1)

    def load(self):
        visible = set(get_config().setting['dlmtags_visible_tags'])
        for tag, check in self.checks.items():
            check.setChecked(tag in visible)

    def save(self):
        config = get_config()
        config.setting['dlmtags_visible_tags'] = [tag for tag in self.checks if self.checks[tag].isChecked()]
        window = self.tagger.window
        metadata_box = getattr(window, 'metadata_box', None) if window is not None else None
        if metadata_box is not None:
            metadata_box.update()

    def _restore_visible_defaults(self):
        for tag, check in self.checks.items():
            check.setChecked(tag in DEFAULT_VISIBLE_TAGS)


register_options_page(DLMtagsApiKeysOptionsPage)
register_options_page(VisibleTagsOptionsPage)
register_options_page(DLMtagsGeneralOptionsPage)
register_options_page(DLMtagsFileSettingsOptionsPage)
