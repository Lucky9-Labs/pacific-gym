#!/usr/bin/env python3
"""Restore a version-pinned animation dummy without overwriting local edits."""
import argparse
import hashlib
import json
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / 'releases/strokah-animation-dummy-v2.json'


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def restore(archive_path, release, destination):
    artifact = release['artifacts']['bundle']
    if archive_path.stat().st_size != artifact['bytes'] or sha256(archive_path) != artifact['sha256']:
        raise ValueError('Bundle checksum or size mismatch; nothing restored')
    destination = destination.resolve()
    expected = {entry['path']: entry for entry in release['files']}
    if len(expected) != len(release['files']):
        raise ValueError('Duplicate paths in release manifest')
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if len(members) != len(expected) or {m.filename for m in members} != set(expected):
            raise ValueError('Archive contents differ from the pinned manifest')
        plan = []
        for member in members:
            path = PurePosixPath(member.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in member.filename or ':' in member.filename:
                raise ValueError(f'Unsafe archive path: {member.filename}')
            if member.is_dir() or stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError(f'Unsupported archive entry: {member.filename}')
            target = destination.joinpath(*path.parts)
            if target.is_symlink() or not target.resolve().is_relative_to(destination):
                raise ValueError(f'Destination escapes through a symlink: {target}')
            record = expected[member.filename]
            data = archive.read(member)
            if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
                raise ValueError(f'File checksum mismatch: {member.filename}')
            if target.exists():
                if not target.is_file() or sha256(target) != record['sha256']:
                    raise ValueError(f'Local file differs; nothing restored: {target}')
            else:
                plan.append((member, target))
        for member, target in plan:
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
    print(f"Verified {release['release']}: restored {len(plan)} files to {destination}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--profile', help='AWS CLI profile with private art-bucket read access')
    parser.add_argument('--from-bundle', type=Path, help='Use an already downloaded bundle')
    args = parser.parse_args()
    release = json.loads(args.manifest.read_text())
    if args.from_bundle:
        restore(args.from_bundle, release, args.destination)
        return
    artifact = release['artifacts']['bundle']
    if not artifact.get('s3_version_id') or artifact['s3_version_id'] == 'null':
        raise ValueError('Release must pin an immutable S3 object version')
    parent = args.destination.resolve().parent
    parent.mkdir(parents=True, exist_ok=True)
    # Keep downloaded bytes on the destination drive, including Windows D:.
    with tempfile.TemporaryDirectory(prefix='.animation-dummy-restore-', dir=parent) as temporary:
        archive = Path(temporary) / 'bundle.zip'
        command = ['aws']
        if args.profile:
            command += ['--profile', args.profile]
        command += ['s3api', 'get-object', '--bucket', release['bucket'],
                    '--key', artifact['object_key'], '--version-id', artifact['s3_version_id'],
                    str(archive), '--output', 'json']
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
        restore(archive, release, args.destination)


if __name__ == '__main__':
    main()
