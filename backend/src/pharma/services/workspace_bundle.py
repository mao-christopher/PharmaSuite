"""Portable app data + Mongo inventory snapshots, with verified non-overwriting restore.

Run export while the app is paused and no uploads/renders are active. Credentials,
logs, Unity caches, and in-progress work are never included.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import zipfile

from bson import BSON

SCHEMA = 'pharma-workspace/1'
SUFFIXES = {'.json', '.jsonl', '.png', '.jpg', '.jpeg', '.webp', '.glb', '.gltf',
            '.obj', '.mtl', '.bin', '.ply', '.stl', '.mp4', '.mov', '.m4v', '.avi',
            '.mkv', '.webm', '.csv', '.npz', '.npy'}


def data_files(root):
    return sorted(p for p in root.rglob('*') if p.is_file() and not p.is_symlink()
                  and p.suffix.lower() in SUFFIXES
                  and p.relative_to(root).parts[0] in ('catalog.json', 'layouts', 'rooms', 'scenarios')
                  and not any(x.startswith('.') or x.endswith('.partial') for x in p.relative_to(root).parts))


def fingerprint(files, root):
    return {p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns) for p in files}


def export_bundle(root, output, collection, pharmacy_id, player):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists(): raise ValueError('Output already exists; choose a new snapshot filename.')
    if output.is_relative_to(root): raise ValueError('Write the archive outside DATA_DIR.')
    inventory = collection.find_one({'_id': pharmacy_id})
    if inventory is None: raise ValueError('No Mongo inventory found for this pharmacy.')
    if player.get('is_playing'): raise ValueError('Pause playback before exporting.')
    if player.get('store', {}).get('revision', inventory['revision']) != inventory['revision']:
        raise ValueError('Player and inventory changed; retry the snapshot.')
    files = data_files(root); before = fingerprint(files, root)
    if 'catalog.json' not in before: raise ValueError('DATA_DIR has no catalog.json.')
    inventory['player_state'] = {k: player.get(k) for k in ('scenario', 'media_time_ms', 'player_source')}
    inventory['current_recording'] = player.get('scenario') or inventory.get('current_recording')
    manifest = {'schema': SCHEMA, 'files': {}, 'player': inventory['player_state'],
                'inventory_revision': inventory['revision']}
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix+'.partial')
    try:
        with zipfile.ZipFile(temp, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for path in files:
                name='data/'+path.relative_to(root).as_posix();content=path.read_bytes()
                manifest['files'][name]={'sha256':hashlib.sha256(content).hexdigest(),
                                         'size':len(content),'mtime_ns':before[name[5:]][1]}
                archive.writestr(name,content)
            content=BSON.encode(inventory)
            manifest['files']['inventory.bson']={'sha256':hashlib.sha256(content).hexdigest(),'size':len(content)}
            archive.writestr('inventory.bson',content)
            archive.writestr('bundle.json',json.dumps(manifest,indent=2)+'\n')
        latest=collection.find_one({'_id':pharmacy_id})
        if latest is None or latest.get('revision')!=manifest['inventory_revision'] or before!=fingerprint(data_files(root),root):
            raise ValueError('Workspace changed during export; retry while uploads, edits and playback are idle.')
        temp.replace(output)
    finally:
        temp.unlink(missing_ok=True)
    return manifest


def restore_bundle(archive_path, target, collection, pharmacy_id):
    target=Path(target).resolve()
    if target.exists(): raise ValueError('Restore requires a new DATA_DIR; existing files are never overwritten.')
    if collection.find_one({'_id':pharmacy_id}) is not None:
        raise ValueError('Target pharmacy already has inventory; use a new database or PHARMACY_ID.')
    target.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.pharma-restore-',dir=target.parent))
    moved=False
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names=archive.namelist()
            if len(names)!=len(set(names)): raise ValueError('Duplicate archive entries.')
            manifest=json.loads(archive.read('bundle.json'))
            if manifest.get('schema')!=SCHEMA: raise ValueError('Unsupported workspace bundle.')
            if set(names)!=set(manifest['files'])|{'bundle.json'}: raise ValueError('Archive file list mismatch.')
            if 'data/catalog.json' not in names or 'inventory.bson' not in names: raise ValueError('Incomplete workspace bundle.')
            inventory=None
            for name,meta in manifest['files'].items():
                path=PurePosixPath(name)
                if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
                    raise ValueError('Unsafe archive path.')
                if name!='inventory.bson' and (len(path.parts)<2 or path.parts[0]!='data'):
                    raise ValueError('Unexpected archive entry.')
                info=archive.getinfo(name)
                if stat.S_ISLNK(info.external_attr>>16): raise ValueError('Archive symlinks are not allowed.')
                content=archive.read(name)
                if len(content)!=meta['size'] or hashlib.sha256(content).hexdigest()!=meta['sha256']:
                    raise ValueError('Archive integrity check failed: '+name)
                if name=='inventory.bson':inventory=BSON(content).decode();continue
                dest=staging.joinpath(*path.parts[1:]);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(content)
                # Floor-track/render caches key the pose-file timestamp; preserve it exactly.
                os.utime(dest,ns=(meta['mtime_ns'],meta['mtime_ns']))
            inventory['_id']=pharmacy_id
            staging.rename(target);moved=True
            try:collection.insert_one(inventory)
            except Exception:
                shutil.rmtree(target);moved=False;raise
        return manifest
    finally:
        if not moved:shutil.rmtree(staging,ignore_errors=True)
