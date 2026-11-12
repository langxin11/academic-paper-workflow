import tempfile
import base64
import unittest
from pathlib import Path
from unittest.mock import patch
import stat
import zipfile
from academic_paper_workflow import parse_cache as cache
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')


class ParseCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.job = self.root / 'job'

    def archive(self, entries):
        path = self.root / 'input.zip'
        with zipfile.ZipFile(path, 'w') as z:
            for name, content in entries.items():
                entry = zipfile.ZipInfo('placeholder')
                entry.filename = name
                z.writestr(entry, content)
        return path

    def publish(self, entries):
        return cache.publish_archive(self.archive(entries), self.job)

    def test_zotero_content_list_only_image_references(self):
        entries = {'full.md': 'Figure captions',
                   'content_list.json': '[{"pages":[{"img_path":"./images/fig.png"}]}]'}
        with self.assertRaises(cache.MaterialValidationError):
            self.publish(entries)
        self.assertIsNone(cache.cached_result(self.job))
        entries['images/fig.png'] = PNG
        full = self.publish(entries)
        self.assertEqual(cache.cached_result(self.job), full)

    def test_publish_nested_and_revalidate(self):
        result = self.publish({'paper/full.md': 'text ![](images/a.png)', 'paper/images/a.png': PNG})
        self.assertEqual(cache.cached_result(self.job), result)
        result.parent.joinpath('images/a.png').unlink()
        self.assertIsNone(cache.cached_result(self.job))

    def test_unsafe_paths(self):
        for name in ['../full.md', '/full.md', 'C:/full.md', 'a\\full.md', 'NUL.txt', 'a./full.md', 'a:stream', 'a/../full.md']:
            with self.subTest(name=name), self.assertRaises(cache.MaterialValidationError):
                self.publish({name: 'text'})

    def test_failed_publish_preserves_good_generation(self):
        good = self.publish({'full.md': 'good'})
        with self.assertRaises(cache.MaterialValidationError):
            self.publish({'full.md': '![](missing.png)'})
        self.assertEqual(cache.cached_result(self.job), good)
        self.assertFalse(list(self.job.glob('.extract-*')))

    def test_invalid_material(self):
        for text in [b'\xff', b' \n', b'![](../outside.png)', b'![](file:///tmp/a.png)', b'![][unknown]']:
            with self.subTest(text=text), self.assertRaises(cache.MaterialValidationError):
                self.publish({'full.md': text})

    def test_multiple_candidates_explained(self):
        with self.assertRaisesRegex(cache.MaterialValidationError, 'a/full.md.*b/full.md'):
            self.publish({'a/full.md': 'a', 'b/full.md': 'b'})

    def test_symlink_archive(self):
        z = self.root / 'link.zip'
        with zipfile.ZipFile(z, 'w') as bundle:
            entry = zipfile.ZipInfo('full.md')
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(entry, 'outside')
        with self.assertRaises(cache.MaterialValidationError):
            cache.publish_archive(z, self.job)

    def test_limits(self):
        with patch.object(cache, 'MAX_FILE_BYTES', 3), self.assertRaises(cache.MaterialValidationError):
            self.publish({'full.md': 'long'})

    def test_case_collisions(self):
        with self.assertRaises(cache.MaterialValidationError):
            self.publish({'full.md': 'a', 'FULL.md': 'b'})

    def test_nonempty_corruption_is_miss(self):
        result = self.publish({'full.md': 'good'})
        result.write_text('evil')
        self.assertIsNone(cache.cached_result(self.job))

    def test_bad_image(self):
        with self.assertRaises(cache.MaterialValidationError):
            self.publish({'full.md': '![](a.png)', 'a.png': b'broken'})

    def test_reference_and_html_images(self):
        result = self.publish({'full.md': '![][fig]\n[fig]: images/a.png\n<img src="images/a.png">', 'images/a.png': PNG})
        self.assertTrue(result.is_file())

    def test_pointer_escape_is_miss(self):
        self.root.joinpath('current.json').write_text('{"generation":"../other"}')
        self.assertIsNone(cache.cached_result(self.root))

    def test_crc_corruption(self):
        z = self.archive({'full.md': 'abcdef'})
        data = z.read_bytes().replace(b'abcdef', b'abcdeg', 1)
        z.write_bytes(data)
        with self.assertRaises(cache.MaterialValidationError):
            cache.publish_archive(z, self.job)


if __name__ == '__main__':
    unittest.main()
