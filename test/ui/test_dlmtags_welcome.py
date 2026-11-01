# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

from types import SimpleNamespace
from unittest.mock import patch

from PyQt6 import (
    QtSvg,
    QtWidgets,
)

from test.picardtestcase import PicardTestCase

from picard.config import get_config

from picard.ui.dlmtags.welcome import DLMtagsWelcomeDialog
from picard.ui.mainwindow import MainWindow


class DLMtagsWelcomeDialogTest(PicardTestCase):
    def setUp(self):
        super().setUp()
        DLMtagsWelcomeDialog._shown_this_session = False

    def tearDown(self):
        DLMtagsWelcomeDialog._shown_this_session = False
        super().tearDown()

    def test_greek_english_and_unknown_language_fallback(self):
        translations = {
            'Protect your music library': 'Προστατέψτε τη μουσική σας συλλογή',
            'Welcome to DLMtags': 'Καλώς ήρθατε στο DLMtags',
            'Saving changes in DLMtags can overwrite existing metadata and, depending on your settings, modify filenames.': (
                'Η αποθήκευση αλλαγών στο DLMtags μπορεί να αντικαταστήσει υπάρχοντα μεταδεδομένα και, '
                'ανάλογα με τις ρυθμίσεις, να τροποποιήσει ονόματα αρχείων.'
            ),
            'We recommend:': 'Συνιστούμε:',
            'Backing up your music before making bulk changes.': (
                'Να δημιουργείτε αντίγραφα ασφαλείας πριν από μαζικές αλλαγές.'
            ),
            'Reviewing proposed tags before confirming.': 'Να ελέγχετε τις προτεινόμενες ετικέτες πριν από την επιβεβαίωση.',
            'Testing with a small number of copied files first.': 'Να κάνετε πρώτα δοκιμές σε λίγα αντίγραφα αρχείων.',
            'DLMtags is free, open-source software based on MusicBrainz Picard and is provided without warranty.': (
                'Το DLMtags είναι δωρεάν λογισμικό ανοιχτού κώδικα, βασισμένο στο MusicBrainz Picard, '
                'και παρέχεται χωρίς εγγύηση.'
            ),
            "Don't show again": 'Να μην εμφανιστεί ξανά',
            'Continue': 'Συνέχεια',
        }

        def configured_translation(message):
            return translations.get(message, message) if get_config().setting['ui_language'] == 'el' else message

        with patch('picard.ui.dlmtags.welcome._', side_effect=configured_translation):
            self.set_config_values({'ui_language': 'el'})
            dialog = DLMtagsWelcomeDialog()
            self.addCleanup(dialog.deleteLater)
            self.assertEqual('Καλώς ήρθατε στο DLMtags', dialog.windowTitle())
            self.assertEqual('Καλώς ήρθατε στο DLMtags', dialog.title_label.text())
            self.assertIn('Η αποθήκευση αλλαγών', dialog.intro_label.text())
            self.assertEqual(
                'Συνιστούμε:', dialog.findChild(QtWidgets.QLabel, 'dlmtagsWelcomeRecommendationHeading').text()
            )
            self.assertEqual(3, len(dialog.recommendation_labels))
            self.assertIn('Να ελέγχετε', dialog.recommendation_labels[1].text())
            self.assertIn('χωρίς εγγύηση', dialog.license_note.text())
            self.assertEqual('Να μην εμφανιστεί ξανά', dialog.dont_show_again.text())
            self.assertEqual('Συνέχεια', dialog.continue_button.text())
            dialog.deleteLater()

            self.set_config_values({'ui_language': 'fr'})
            fallback = DLMtagsWelcomeDialog()
            self.addCleanup(fallback.deleteLater)
            self.assertEqual('Welcome to DLMtags', fallback.windowTitle())
            self.assertIn('Saving changes in DLMtags', fallback.intro_label.text())
            self.assertEqual(
                'We recommend:', fallback.findChild(QtWidgets.QLabel, 'dlmtagsWelcomeRecommendationHeading').text()
            )
            self.assertEqual("Don't show again", fallback.dont_show_again.text())

    def test_logo_is_registered_and_keeps_its_aspect_ratio(self):
        source = QtSvg.QSvgRenderer(':/images/dlmtags-logo.svg')
        self.assertTrue(source.isValid())
        dialog = DLMtagsWelcomeDialog()
        self.addCleanup(dialog.deleteLater)
        displayed = dialog.findChild(QtWidgets.QLabel, 'dlmtagsWelcomeLogo').pixmap()
        self.assertIsNotNone(displayed)
        self.assertAlmostEqual(
            source.viewBoxF().width() / source.viewBoxF().height(),
            displayed.width() / displayed.height(),
            delta=0.01,
        )
        self.assertGreaterEqual(displayed.width(), 110)
        self.assertLessEqual(displayed.width(), 130)
        self.assertEqual(700, dialog.width())
        self.assertGreaterEqual(dialog.height(), 500)
        self.assertLess(dialog.height(), 650)

    def test_checkbox_persistence_and_once_per_session(self):
        config = get_config()
        config.setting['show_new_user_dialog'] = True
        window = SimpleNamespace(modern_workspace=object())
        with patch('picard.ui.mainwindow.DLMtagsWelcomeDialog') as dialog_class:
            dialog_class.return_value.show_startup_warning.return_value = False
            MainWindow.show_new_user_dialog(window, config)
            self.assertFalse(config.setting['show_new_user_dialog'])

        config.setting['show_new_user_dialog'] = True
        first = DLMtagsWelcomeDialog()
        self.addCleanup(first.deleteLater)
        first.dont_show_again.setChecked(True)
        with patch.object(first, 'exec') as exec_dialog:
            self.assertFalse(first.show_startup_warning())
        exec_dialog.assert_called_once()

        second = DLMtagsWelcomeDialog()
        self.addCleanup(second.deleteLater)
        with patch.object(second, 'exec') as exec_dialog:
            self.assertIsNone(second.show_startup_warning())
        exec_dialog.assert_not_called()
