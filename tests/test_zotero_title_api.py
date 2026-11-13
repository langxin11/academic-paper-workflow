import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from academic_paper_workflow import zotero_title_api as api
from academic_paper_workflow.zotero_titles import TitleResolutionError


class ApiTitleTests(unittest.TestCase):
    def test_pagination(self):
        with patch.object(api, '_get', side_effect=[[{}] * 100, [{}]]) as get:
            self.assertEqual(len(list(api._pages('/api/users/0/items'))), 101)
            self.assertIn('start=100', get.call_args.args[0])

    def test_local_file_only(self):
        self.assertIsNone(api._file_path('https://example.org/a.pdf'))
        self.assertIsNone(api._file_path('file://server/share/a.pdf'))
        self.assertIsNone(api._file_path('file:///C:/a.pdf?redirect=1'))

    def test_exact_pdf_and_group_ambiguity(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / 'a.pdf'
            pdf.write_bytes(b'%PDF-1.4')
            parent = {'key': 'ABCDEFGH', 'data': {'title': 'A paper', 'date': '2024', 'itemType': 'journalArticle'}}
            child = {'key': 'SJ552ALW', 'data': {'itemType': 'attachment', 'contentType': 'application/pdf', 'filename': 'a.pdf'}}
            def pages(route):
                if route.endswith('/groups'):
                    return iter([])
                return iter([child] if '/children' in route else [parent])
            with patch.object(api, '_pages', side_effect=pages), patch.object(api, '_get', return_value=pdf.as_uri()):
                self.assertEqual(api.resolve_api_title('A PAPER', Path(folder)).pdf, pdf)
            def groups(route):
                if route.endswith('/groups'):
                    return iter([{'id': 12}])
                return iter([child] if '/children' in route else [parent])
            with patch.object(api, '_pages', side_effect=groups), patch.object(api, '_get', return_value=pdf.as_uri()):
                with self.assertRaises(TitleResolutionError):
                    api.resolve_api_title('A paper', Path(folder))

    def test_multiple_pdfs_rejected(self):
        parent = {'key': 'ABCDEFGH', 'data': {'title': 'A paper', 'itemType': 'journalArticle'}}
        child = {'key': 'SJ552ALW', 'data': {'itemType': 'attachment', 'contentType': 'application/pdf'}}
        def pages(route):
            return iter([] if route.endswith('/groups') else [child, child] if '/children' in route else [parent])
        with patch.object(api, '_pages', side_effect=pages), patch.object(api, '_get', return_value='http://evil.test/a.pdf'):
            with self.assertRaises(TitleResolutionError):
                api.resolve_api_title('A paper', Path('.'))

    def test_redirects_refused(self):
        self.assertIsNone(api._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.org'))

    def test_wrong_root_rejected_and_book_quotes_normalized(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pdf = root / 'storage' / 'SJ552ALW' / 'a.pdf'
            pdf.parent.mkdir(parents=True)
            pdf.write_bytes(b'%PDF')
            parent = {'key': 'ABCDEFGH', 'data': {'title': 'Impact-aware objects', 'itemType': 'journalArticle'}}
            unrelated = {'key': 'ZZZZZZZZ', 'data': {'title': 'Other objects', 'itemType': 'journalArticle'}}
            child = {'key': 'SJ552ALW', 'data': {'itemType': 'attachment', 'contentType': 'application/pdf', 'linkMode': 'imported_file'}}
            calls = []
            def pages(route):
                calls.append(route)
                return iter([] if route.endswith('/groups') else [child] if '/children' in route else [parent, unrelated])
            with patch.object(api, '_pages', side_effect=pages), patch.object(api, '_get', return_value=pdf.as_uri()):
                self.assertEqual(api.resolve_api_title('《IMPACT—AWARE OBJECTS》', root).pdf, pdf)
                self.assertFalse(any('/ZZZZZZZZ/children' in route for route in calls))
                with self.assertRaisesRegex(TitleResolutionError, 'does not match'):
                    api.resolve_api_title('《Impact-aware objects》', root / 'wrong')


if __name__ == '__main__':
    unittest.main()
