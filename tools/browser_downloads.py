"""Download a frozen URL manifest through the installed Tabbit launcher."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from shutil import copy2
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--suffix', default='.png')
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=True)
    jobs = json.loads(args.manifest.read_text(encoding='utf-8'))
    launcher = Path(os.environ['LOCALAPPDATA']) / 'Tabbit/LocalAgent/bin/tabbit-cli.exe'
    env = dict(os.environ, TABBIT_PLAYWRIGHT_INSTANCE='F8D3D3D1B557FCF5')
    pending = [job for job in jobs if not (args.destination / (job['sample_id'] + args.suffix)).exists()]
    failures = []
    for offset in range(0, len(pending), 6):
        batch = pending[offset:offset + 6]
        program = ('const jobs=' + json.dumps(batch, ensure_ascii=True) + ';\n'
                   'return await Promise.all(jobs.map(async (job,i)=>{\n'
                   'try { const r=await page.fetch(encodeURI(job.url),'
                   '{as:"file",savePath:artifactPath("data_' + str(time.time_ns()) + '_"+i+".bin"),'
                   'timeoutMs:40000,maxBytes:8000000});\n'
                   'return {sample_id:job.sample_id,ok:r.ok,status:r.status,path:r.path,bytes:r.bytes};'
                   '} catch(e) {return {sample_id:job.sample_id,ok:false,error:String(e)};}\n'
                   '}));')
        process = subprocess.run([str(launcher), 'nodejs', '--task', 'RankStab expanded validation',
                                  '--timeout-ms', '55000'], input=program, capture_output=True,
                                 encoding='utf-8', env=env, timeout=75)
        try:
            receipt = json.loads(process.stdout)
            result = receipt['result']
            values = result.get('value')
            if values is None:
                values = json.loads(Path(result['path']).read_text(encoding='utf-8'))
            for item in values:
                if item.get('ok'):
                    copy2(item['path'], args.destination / (item['sample_id'] + args.suffix))
                else:
                    failures.append(item)
            print('Downloaded %d/%d, failures %d' % (min(offset + 6, len(pending)), len(pending), len(failures)), flush=True)
        except Exception as exc:
            failures.extend(dict(job, error=str(exc)) for job in batch)
            print(process.stdout[-1500:], process.stderr[-500:], flush=True)
    (args.destination / 'download_failures.json').write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding='utf-8')
    print('DOWNLOADS_COMPLETE failures=%d' % len(failures), flush=True)


if __name__ == '__main__':
    main()
