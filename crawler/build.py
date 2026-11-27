"""生成交付 ZIP、文件清单和 ZIP 哈希，不包含环境或缓存。"""
import hashlib
import json
import zipfile
from pathlib import Path
from crawler import __version__ as VERSION

root = Path(__file__).resolve().parent
out = root.parent/'deliverables'
out.mkdir(exist_ok=True)
files = [p for p in root.iterdir() if p.is_file() and p.suffix in ('.py','.md','.txt','.ps1')]
for folder in ('crawler','tests','config'):
    files += [p for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
sample = root/'data/szse'
files += [sample/name for name in ('state.db','source.json','first_run.json')]
files += list((sample/'raw').glob('*'))
files += [sample/'staging/documents.json', sample/'staging/parsed_v1/chunks.json', sample/'staging/parsed_v1/report.json', sample/'staging/parsed_v1/documents.json']
files += list((root/'review/20261120').glob('*.md'))
files += list((root/'review/20261120').glob('*.json'))
files += list((root/'review/20261120').glob('*.png'))
files += list((sample/'sandbox').glob('*.json'))
files += list((sample/'sandbox').glob('*.jsonl'))
manifest = [{'path':p.relative_to(root).as_posix(), 'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
             'bytes':p.stat().st_size} for p in sorted(set(files))]
manifest_path = root/'FILE_MANIFEST.json'
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
target = out/f'crawler-{VERSION}.zip'
with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
    for p in sorted(set(files)) + [manifest_path]:
        archive.write(p, 'crawler/'+p.relative_to(root).as_posix())
sha = hashlib.sha256(target.read_bytes()).hexdigest()
(out/f'crawler-{VERSION}.sha256').write_text(sha+'  '+target.name+'\n', encoding='utf-8')
print(json.dumps({'zip':str(target),'sha256':sha,'files':len(manifest)+1}, indent=2))
