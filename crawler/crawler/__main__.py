import argparse
import json
from pathlib import Path
from .config import load
from .engine import run
from .store import Store


def main():
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='金融制度爬虫：原件采集与待审清单导出')
    parser.add_argument('command', choices=['run', 'export', 'parse', 'verify', 'status', 'sandbox', 'evaluate'])
    parser.add_argument('--cases', default='config/qa_cases.json')
    parser.add_argument('--config', default='config/example.json')
    parser.add_argument('--data', default='data')
    parser.add_argument('--refresh', action='store_true', help='重新抓取已完成任务，检测新版本')
    args = parser.parse_args()
    root = Path(args.data).resolve()
    root.mkdir(parents=True, exist_ok=True)
    # 操作系统文件锁：进程崩溃自动释放，无需删除遗留锁文件。
    with (root / 'run.lock').open('a+b') as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        import os
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            parser.exit(2, '该数据目录已有运行中的进程\n')
        store = Store(root)
        try:
            if args.command == 'run':
                config = load(args.config)
                # 防止不同来源配置在同一个任务库中混用。
                saved = root / 'source.json'
                identity = {'name':config['name'], 'allowed_hosts':config['allowed_hosts']}
                if saved.exists() and json.loads(saved.read_text('utf-8')) != identity:
                    raise ValueError('数据目录已绑定其他来源，请使用不同 --data')
                saved.write_text(json.dumps(identity), encoding='utf-8')
                result = run(store, config, args.refresh)
            elif args.command == 'export':
                result = {'exported':len(store.export()['documents']), 'manifest':str(root/'staging/documents.json')}
            elif args.command == 'parse':
                from .parse import parse_manifest
                parsed = parse_manifest(root/'staging/documents.json', root/'staging/parsed_v1')
                result = {'chunks':parsed['chunk_count'], 'failed':sum(d['status']=='failed' for d in parsed['documents']), 'released':0}
            elif args.command == 'verify':
                from .verify import verify
                result = verify(root)
            elif args.command == 'sandbox':
                from .rag import export_sandbox
                result = export_sandbox(root)
            elif args.command == 'evaluate':
                from .rag import evaluate
                result = evaluate(root, args.cases)
            else:
                result = dict(store.db.execute('SELECT status,count(*) FROM tasks GROUP BY status').fetchall())
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if ((args.command == 'run' and any(result['states'].get(s) for s in ('retry','quarantined','pending'))) or (args.command == 'parse' and result['failed'])) else 0
        finally:
            store.db.close()


if __name__ == '__main__':
    raise SystemExit(main())
