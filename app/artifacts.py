"""Immutable render evidence. Paths never come from model output or API callers."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import threading
import time

ARTIFACT_LOCK = threading.Lock()


def prune_evidence(directory, protected=(), max_bytes=1024*1024*1024, max_age=7*86400, reserve=0):
    """Bound PNG storage independently of permanent run metadata; never delete active evidence."""
    root = Path(directory) / 'artifacts'
    with ARTIFACT_LOCK:
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        protected = {str(value) for value in protected}
        entries=[]
        for path in root.iterdir():
            if not path.is_dir() or path.is_symlink() or not path.name.isdigit():
                continue
            files=[f for f in path.iterdir() if f.is_file() and not f.is_symlink()]
            entries.append((path.stat().st_mtime,path,sum(f.stat().st_size for f in files)))
        total=sum(size for _,_,size in entries)
        for modified,path,size in sorted(entries):
            if path.name not in protected and (modified < time.time()-max_age or total+reserve > max_bytes):
                shutil.rmtree(path)
                total -= size
        if total+reserve > max_bytes or shutil.disk_usage(root).free < reserve+64*1024*1024:
            raise OSError('Evidence storage budget exhausted')


def save_evidence(directory, run_id, svg, frames, metadata):
    root = Path(directory) / 'artifacts' / str(int(run_id))
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    manifest = dict(metadata, svg_sha256=hashlib.sha256(svg.encode()).hexdigest(), frames=[])
    total = 0
    for index, frame in enumerate(frames):
        png = base64.b64decode(frame['png'], validate=True)
        total += len(png)
        if total > 16 * 1024 * 1024 or not png.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('Invalid or oversized frame evidence')
        name = f'{index}.png'
        atomic_write(root / name, png)
        manifest['frames'].append({'index': index, 'time': frame['time'],
                                   'sha256': hashlib.sha256(png).hexdigest(), 'bytes': len(png)})
    atomic_write(root / 'manifest.json', json.dumps(manifest, ensure_ascii=False).encode())
    return manifest


def atomic_write(path, data):
    temp = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
    try:
        with temp.open('xb') as file:
            os.chmod(temp, 0o600)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def load_evidence(directory, run_id, svg):
    """Reload the exact original frames, rejecting expiry, replacement and corruption."""
    root = Path(directory) / 'artifacts' / str(int(run_id))
    manifest = json.loads((root / 'manifest.json').read_text())
    if manifest.get('svg_sha256') != hashlib.sha256(svg.encode()).hexdigest():
        raise ValueError('SVG does not match persisted evidence')
    entries = manifest.get('frames',[])
    if not 12 <= len(entries) <= 16:
        raise ValueError('Incomplete evidence')
    frames, total = [], 0
    for index, entry in enumerate(entries):
        path = root / f'{index}.png'
        if path.is_symlink() or path.stat().st_size > 16*1024*1024:
            raise ValueError('Invalid evidence file')
        png = path.read_bytes()
        total += len(png)
        if (total > 16*1024*1024 or entry.get('index') != index or entry.get('bytes') != len(png)
                or entry.get('sha256') != hashlib.sha256(png).hexdigest()
                or not png.startswith(b'\x89PNG\r\n\x1a\n')):
            raise ValueError('Evidence hash or size mismatch')
        frames.append({'time':entry['time'],'png':base64.b64encode(png).decode()})
    return {'frames':frames,'metadata':manifest}
