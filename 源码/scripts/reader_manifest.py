"""Record the source and built assets used by the second-phase release."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.tmp/qa/reader'
folders = ['backend/app', 'frontend/web/src', 'frontend/web/public', 'frontend/web/dist',
           'docker/nginx', 'docker/docker-entrypoint.d']
files = {path for name in folders for path in (ROOT / name).rglob('*')
         if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'}
for name in ['backend/requirements.txt', 'pyproject.toml', '.dockerignore',
             'frontend/web/package.json', 'frontend/web/package-lock.json',
             'frontend/web/pnpm-lock.yaml', 'frontend/web/vite.config.ts',
             'frontend/web/tsconfig.json', 'docker/Dockerfile.backend.incremental',
             'docker/Dockerfile.frontend.incremental']:
    path = ROOT / name
    if path.exists(): files.add(path)
entries = [{'path': p.relative_to(ROOT).as_posix(), 'bytes': p.stat().st_size,
            'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(files)]
digest = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
now = datetime.now().astimezone()
manifest = {'tag': now.strftime('v1.3.0-%Y%m%d-%H%M'), 'created_at': now.isoformat(),
    'source_manifest_sha256': digest, 'git_head': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
    'baseline': 'existing uncommitted working tree; changes retained',
    'file_count': len(entries), 'files': entries}
OUT.mkdir(parents=True, exist_ok=True)
(OUT / '源码与构建清单.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k:v for k,v in manifest.items() if k!='files'}, ensure_ascii=False))
