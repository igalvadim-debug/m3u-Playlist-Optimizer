"""Regression tests for untrusted playlist labels in PDF paragraphs."""
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import reportlab
from reportlab.platypus import Paragraph, SimpleDocTemplate

from modules.converter import M3UConverter


class ConverterPDFTests(unittest.TestCase):
    def setUp(self):
        font = Path(reportlab.__file__).parent / 'fonts' / 'Vera.ttf'
        self.converter = M3UConverter(str(font))

    def test_pdf_labels_are_literal_text(self):
        labels = [
            "<img src='http://127.0.0.1:9/private.png'/>",
            "<img src='file:///private/image.png'/>",
            '<b>Каналы & ТВ</b> <broken',
            '&lt;img src="secret.png"/&gt; &#60;b&#62;',
        ]
        for label in labels:
            with self.subTest(label=label):
                with patch('reportlab.platypus.paraparser.ImageReader') as reader:
                    story = self.converter.build_pdf_content({label: {label: [label]}})
                    empty_story = self.converter.build_pdf_content({label: {}})
                    reader.assert_not_called()
                paragraphs = [p for p in story if isinstance(p, Paragraph)]
                self.assertEqual([p.getPlainText() for p in paragraphs],
                                 [f'📁 {label}', f'🔹 {label}', label])
                self.assertEqual(empty_story[0].getPlainText(), f'📁 {label}')

    def test_uploaded_metadata_converts_to_pdf_without_loading_images(self):
        group = "<img src='http://127.0.0.1:9/group.png'/>"
        channel = "<img src='http://127.0.0.1:9/channel.png'/>"
        normal = 'Новости & ТВ <HD> &lt;test&gt;'
        with tempfile.TemporaryDirectory() as folder:
            playlist = Path(folder) / '<Каналы & ТВ>.m3u'
            playlist.write_text(
                f'#EXTM3U\n#EXTINF:-1 group-title="{group}",{channel}\n'
                f'http://stream.invalid/1\n'
                f'#EXTINF:-1 group-title="{group}",{normal}\n'
                f'http://stream.invalid/2\n', encoding='utf-8')
            self.assertEqual(self.converter.parse_m3u(playlist),
                             {group: [channel, normal]})
            with patch('reportlab.platypus.paraparser.ImageReader') as reader:
                story, _, _ = self.converter.convert_to_formats([str(playlist)], 'playlist')
                paragraphs = [p.getPlainText() for p in story if isinstance(p, Paragraph)]
                self.assertEqual(paragraphs,
                                 [f'📁 {playlist.name}', f'🔹 {group}', channel, normal])
                document = io.BytesIO()
                SimpleDocTemplate(document).build(story)
                reader.assert_not_called()
                self.assertTrue(document.getvalue().startswith(b'%PDF-'))


class ConverterFontFallbackTests(unittest.TestCase):
    def test_fallback_skips_latin_only_font(self):
        with tempfile.TemporaryDirectory() as folder:
            latin = Path(folder) / 'a-latin.ttf'
            cyrillic = Path(folder) / 'b-cyrillic.ttf'
            latin.touch()
            cyrillic.touch()
            required = set(range(0x0410, 0x0450)) | {0x0401, 0x0451}
            fonts = [Mock(face=Mock(charToGlyph={ord('A'): 1})),
                     Mock(face=Mock(charToGlyph=dict.fromkeys(required, 1)))]
            with patch('modules.converter.TTFont', side_effect=fonts), \
                    patch('modules.converter.pdfmetrics.registerFont') as register:
                converter = M3UConverter(str(Path(folder) / 'missing.ttf'))
            self.assertEqual(converter.font_path, str(cyrillic))
            register.assert_called_once_with(fonts[1])

    def test_missing_font_and_vera_fallback_fail_clearly(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = str(Path(folder) / 'DejaVuSans.ttf')
            for add_vera in (False, True):
                if add_vera:
                    vera = Path(reportlab.__file__).parent / 'fonts' / 'Vera.ttf'
                    shutil.copyfile(vera, Path(folder) / 'Vera.ttf')
                with self.subTest(vera=add_vera), \
                        patch('modules.converter.pdfmetrics.registerFont') as register:
                    with self.assertRaisesRegex(FileNotFoundError, 'кириллицы.*DejaVuSans.ttf'):
                        M3UConverter(missing)
                    register.assert_not_called()


if __name__ == '__main__':
    unittest.main()
