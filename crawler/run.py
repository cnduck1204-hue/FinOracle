"""跨平台一键入口，默认离线复验附带样本；--online 才访问采集站点。"""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--online', action='store_true')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--data', default='data/szse')
    parser.add_argument('--config', default='config/szse.json')
    parser.add_argument('--evaluate', action='store_true', help='运行附带的 10 个固定样本问题，仅适用于样本数据')
    args = parser.parse_args()
    if args.refresh and not args.online:
        parser.error('--refresh 需要 --online')
    root = Path(__file__).resolve().parent
    data = (root/args.data).resolve()
    if not args.online and not (data/'state.db').is_file():
        parser.error('离线数据不存在；使用交付样本，或加 --online 从站点采集')
    commands = []
    if args.online:
        cmd = ['run','--config',args.config]
        if args.refresh:
            cmd.append('--refresh')
        commands.append(cmd)
    commands.extend([[step] for step in ('export','parse','verify','sandbox')])
    if args.evaluate:
        commands.append(['evaluate','--cases',str(root/'config/qa_cases.json')])
    for command in commands:
        result = subprocess.run([sys.executable,'-m','crawler',*command,'--data',str(data)], cwd=root)
        if result.returncode:
            print(f'步骤 {command[0]} 失败，后续步骤未执行。', file=sys.stderr)
            return result.returncode
    print(json.dumps({'result':'completed', 'data':str(data), 'environment':'sandbox', 'released':0}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
