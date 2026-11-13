import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from academic_paper_workflow.zotero_titles import (
    TitleResolutionError, normalize_title, resolve_title, select_candidates,
)


class ZoteroTitlesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.conn = sqlite3.connect(self.root / 'zotero.sqlite')
        self.addCleanup(self.conn.close)
        self.conn.executescript('''
            CREATE TABLE items(itemID INTEGER PRIMARY KEY, key TEXT, libraryID INTEGER, itemTypeID INTEGER);
            CREATE TABLE itemTypes(itemTypeID INTEGER, typeName TEXT);
            CREATE TABLE fields(fieldID INTEGER, fieldName TEXT);
            CREATE TABLE itemData(itemID INTEGER,fieldID INTEGER,valueID INTEGER);
            CREATE TABLE itemDataValues(valueID INTEGER,value TEXT);
            CREATE TABLE deletedItems(itemID INTEGER);
            CREATE TABLE itemAttachments(itemID INTEGER,parentItemID INTEGER,path TEXT,contentType TEXT);
            INSERT INTO itemTypes VALUES(1,'journalArticle'),(2,'attachment'),(3,'note');
            INSERT INTO fields VALUES(1,'title'),(2,'date'),(3,'DOI');
        ''')

    def item(self, ident, title, kind=1):
        self.conn.execute('INSERT INTO items VALUES(?,?,?,?)', (ident, f'ITEM{ident}', 1, kind))
        self.conn.execute('INSERT INTO itemData VALUES(?,?,?)', (ident, 1, ident))
        self.conn.execute('INSERT INTO itemDataValues VALUES(?,?)', (ident, title))
        self.conn.commit()

    def attachment(self, ident, parent, raw=None, exists=True):
        key = f'PDF{ident}'
        self.conn.execute('INSERT INTO items VALUES(?,?,?,?)', (ident,key,1,2))
        self.conn.execute('INSERT INTO itemAttachments VALUES(?,?,?,?)', (ident,parent,raw or 'storage:paper.pdf','application/pdf'))
        path = self.root / 'storage' / key / 'paper.pdf'
        if exists and raw is None:
            path.parent.mkdir(parents=True)
            path.write_bytes(b'%PDF-1.4')
        self.conn.commit()
        return path

    def test_normalization(self):
        self.assertEqual(normalize_title('《<i>Impact–Aware</i> &amp; Catching》'), 'impact aware & catching')
        self.item(1, 'Impact–Aware Catching')
        expected = self.attachment(2,1)
        self.assertEqual(resolve_title('《impact-aware catching》', self.root).pdf, expected)

    def test_duplicate_titles_are_ambiguous(self):
        for ident in (1,2):
            self.item(ident,'Catching')
            self.attachment(ident+10,ident)
        with self.assertRaises(TitleResolutionError) as caught:
            resolve_title('Catching',self.root)
        self.assertEqual(len(caught.exception.candidates),2)
        self.assertIn('PDF11',str(caught.exception))

    def test_multiple_pdfs_and_fuzzy_never_choose(self):
        self.item(1,'Impact aware catching')
        self.attachment(2,1)
        self.attachment(3,1)
        for title in ('Impact aware catching','Impact aware'):
            with self.assertRaises(TitleResolutionError):
                resolve_title(title,self.root)

    def test_trash_parents_notes_and_attachments_excluded(self):
        self.item(1,'Catching')
        self.item(2,'Catching',3)
        self.item(3,'Catching',2)
        self.attachment(4,1)
        self.conn.execute('INSERT INTO deletedItems VALUES(1)')
        self.conn.commit()
        with self.assertRaises(TitleResolutionError) as caught:
            resolve_title('Catching',self.root)
        self.assertFalse(caught.exception.candidates)

    def test_trashed_attachment_ignored(self):
        self.item(1,'Catching')
        expected = self.attachment(2,1)
        self.attachment(3,1)
        self.conn.execute('INSERT INTO deletedItems VALUES(3)')
        self.conn.commit()
        self.assertEqual(resolve_title('Catching',self.root).pdf,expected)

    def test_stale_and_relative_linked_paths(self):
        self.item(1,'Catching')
        self.attachment(2,1,exists=False)
        for raw in ('storage:missing.pdf','attachments:paper.pdf','storage:../../escape.pdf'):
            self.conn.execute('UPDATE itemAttachments SET path=?',(raw,))
            self.conn.commit()
            with self.assertRaises(TitleResolutionError):
                resolve_title('Catching',self.root)

    def test_absolute_linked_file(self):
        linked = self.root / 'linked.pdf'
        linked.write_bytes(b'%PDF-1.4')
        self.item(1,'Catching')
        self.attachment(2,1,str(linked))
        self.assertEqual(resolve_title('Catching',self.root).pdf,linked)

    def test_storage_symlink_rejected(self):
        self.item(1,'Catching')
        self.attachment(2,1)
        original = Path.is_symlink
        with patch.object(Path, 'is_symlink', lambda p: p.name == 'PDF2' or original(p)):
            with self.assertRaises(TitleResolutionError) as caught:
                resolve_title('Catching',self.root)
        self.assertIn('junction or symlink',str(caught.exception))

    def test_blank_title_not_fuzzy_candidate(self):
        self.item(1,'')
        with self.assertRaises(TitleResolutionError) as caught:
            resolve_title('Catching',self.root)
        self.assertEqual(caught.exception.candidates,[])

    def test_distinct_chinese_error_messages(self):
        def candidate(title, key='KEY', attachments=None):
            return dict(title=title, key=key, year=None, doi=None, attachments=attachments or [])
        cases = [
            ([], '未找到'),
            ([candidate('Completely unrelated')], '未找到'),
            ([candidate('Catching objects')], '近似候选'),
            ([candidate('Catching')], '未发现 PDF'),
            ([candidate('Catching'), candidate('Catching', 'OTHER')], '同名条目'),
        ]
        for candidates, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(TitleResolutionError) as caught:
                select_candidates('Catching', candidates)
            self.assertIn(expected, str(caught.exception))

    def test_fuzzy_candidates_bounded_and_attachments_not_claimed_missing(self):
        candidates = [dict(title=f'Catching objects {i}', key=str(i), year=None, doi=None, attachments=[]) for i in range(20)]
        with self.assertRaises(TitleResolutionError) as caught:
            select_candidates('Catching objects', candidates)
        self.assertEqual(len(caught.exception.candidates), 5)
        self.assertIn('未检查附件', str(caught.exception))
        self.assertNotIn('未发现 PDF', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
