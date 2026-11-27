import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CLITests(unittest.TestCase):
    def test_empty_manifest_parse_and_verify(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'staging').mkdir()
            (root/'staging/documents.json').write_text('{"documents": []}', encoding='utf-8')
            for command in ('parse', 'verify'):
                result = subprocess.run([sys.executable, '-m', 'crawler', command, '--data', temp],
                                        capture_output=True, encoding='utf-8')
                self.assertEqual(result.returncode, 0, result.stderr)
                output = json.loads(result.stdout)
                self.assertNotIn('done', output)
            self.assertEqual(output['result'], 'passed')
