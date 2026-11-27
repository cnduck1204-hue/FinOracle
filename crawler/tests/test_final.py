import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from crawler.rag import export_sandbox


class FinalTests(unittest.TestCase):
    def test_failed_verification_invalidates_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'sandbox'
            folder.mkdir()
            (folder/'manifest.json').write_text('{"status":"completed"}',encoding='utf-8')
            with patch('crawler.rag.verify',side_effect=ValueError('原件不一致')):
                with self.assertRaises(ValueError):export_sandbox(tmp)
            self.assertNotEqual(json.loads((folder/'manifest.json').read_text('utf-8'))['status'],'completed')

    def test_offline_entry_rejects_missing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=subprocess.run([sys.executable,'run.py','--data',tmp],capture_output=True)
            self.assertNotEqual(p.returncode,0)
            self.assertFalse((Path(tmp)/'state.db').exists())
