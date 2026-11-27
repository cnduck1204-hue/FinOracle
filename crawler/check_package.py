"""在解压目录执行，核验交付文件完整性。"""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
manifest = json.loads((root/'FILE_MANIFEST.json').read_text('utf-8'))
for entry in manifest:
    path = (root/entry['path']).resolve()
    if root not in path.parents:
        raise ValueError('清单路径越界')
    if hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
        raise ValueError('校验失败: '+entry['path'])
print(f'PASS: {len(manifest)} files')
