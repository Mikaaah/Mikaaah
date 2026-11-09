#!/usr/bin/env python3
"""TNK release identity and historical build matching. Python 3.11+, no packages."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MARKER = 'META-INF/tnk-release.json'
SEMVER = re.compile(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(alpha|beta|rc|private)\.([1-9]\d*))?\Z')
SAFE = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')


class ReleaseError(ValueError):
    pass


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def dump(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def inspect_jar(path):
    path = Path(path)
    with zipfile.ZipFile(path) as jar:
        names = jar.namelist()
        if len(names) != len(set(names)):
            raise ReleaseError(f'{path.name}: duplicate ZIP entries')
        if any(n.startswith('/') or '..' in Path(n).parts for n in names):
            raise ReleaseError(f'{path.name}: unsafe archive paths')
        manifest = {}
        if 'META-INF/MANIFEST.MF' in names:
            raw = jar.read('META-INF/MANIFEST.MF').decode('utf-8').replace('\r\n ', '').replace('\n ', '')
            manifest = dict(line.split(': ', 1) for line in raw.splitlines() if ': ' in line)
        if 'META-INF/neoforge.mods.toml' in names:
            loader, metadata = 'neoforge', tomllib.loads(jar.read('META-INF/neoforge.mods.toml').decode('utf-8'))
            mods = metadata.get('mods', [])
            mods = [{'id': m.get('modId'), 'version': m.get('version')} for m in mods]
        elif 'META-INF/mods.toml' in names:
            loader, metadata = 'forge', tomllib.loads(jar.read('META-INF/mods.toml').decode('utf-8'))
            mods = [{'id': m.get('modId'), 'version': m.get('version')} for m in metadata.get('mods', [])]
        elif 'fabric.mod.json' in names:
            loader, metadata = 'fabric', json.loads(jar.read('fabric.mod.json'))
            mods = [{'id': metadata.get('id'), 'version': metadata.get('version')}]
        else:
            raise ReleaseError(f'{path.name}: no supported mod metadata')
        for mod in mods:
            if mod['version'] == '${file.jarVersion}':
                mod['version'] = manifest.get('Implementation-Version')
            if not isinstance(mod['version'], str) or '${' in mod['version']:
                raise ReleaseError(f'{path.name}: unresolved mod version')
        digest = hashlib.sha256()
        max_class_major = 0
        for name in sorted(names):
            if name.endswith('/'):
                continue
            encoded = name.encode('utf-8')
            digest.update(len(encoded).to_bytes(4, 'big'))
            digest.update(encoded)
            entry = jar.read(name)
            digest.update(hashlib.sha256(entry).digest())
            if name.endswith('.class') and not name.startswith('META-INF/versions/') and entry[:4] == b'\xca\xfe\xba\xbe':
                max_class_major = max(max_class_major, int.from_bytes(entry[6:8], 'big'))
        marker = json.loads(jar.read(MARKER)) if MARKER in names else None
    return {'filename': path.name, 'sha256': sha(path.read_bytes()), 'payload_sha256': digest.hexdigest(),
            'loader': loader, 'mods': mods, 'marker': marker, 'max_class_major': max_class_major}


def project(registry, key):
    matches = [p for p in registry['projects'] if p['id'] == key]
    if len(matches) != 1:
        raise ReleaseError(f'Unknown or ambiguous project: {key}')
    return matches[0]


def check_registry(registry):
    if registry.get('schema') != 1:
        raise ReleaseError('Unsupported registry schema')
    ids = set()
    for p in registry['projects']:
        if not SAFE.fullmatch(p['id']) or p['id'] in ids:
            raise ReleaseError('Invalid or duplicate project ID')
        ids.add(p['id'])
        if p['status'] not in ('active', 'development', 'deprecated', 'retired'):
            raise ReleaseError(f"{p['id']}: invalid status")
        seen = {}
        for artifact in p.get('artifacts', []):
            for key in ('sha256', 'payload_sha256'):
                if not re.fullmatch(r'[0-9a-f]{64}', artifact[key]):
                    raise ReleaseError(f"{p['id']}: invalid {key}")
            if artifact['role'] not in ('published', 'development'):
                raise ReleaseError('Invalid artifact role')
            if artifact['mod_id'] not in p.get('mod_ids', []):
                raise ReleaseError('Artifact belongs to a different mod')
            if artifact['target'] not in {t['id'] for t in p.get('targets', [])}:
                raise ReleaseError('Artifact uses an unknown target')
            if artifact.get('file_id'):
                identity = (artifact.get('project_id'), artifact['file_id'])
                if identity in seen and seen[identity] != artifact['sha256']:
                    raise ReleaseError('One published file ID points to conflicting hashes')
                seen[identity] = artifact['sha256']
    return len(ids)


def match_jar(registry, path):
    found = inspect_jar(path)
    exact, repacked = [], []
    for p in registry['projects']:
        for a in p.get('artifacts', []):
            match = {'project': p['id'], **a}
            if a['sha256'] == found['sha256']:
                exact.append(match)
            elif a['payload_sha256'] == found['payload_sha256']:
                repacked.append(match)
    return {'artifact': found, 'exact_matches': exact, 'same_entries_different_zip': repacked,
            'status': 'exact-known-build' if exact else 'repacked-known-build' if repacked else 'unknown-build'}


def load_release(path, registry):
    m = read_json(path)
    if m.get('schema') != 1:
        raise ReleaseError('Unsupported release schema')
    p = project(registry, m['project'])
    if p['status'] in ('deprecated', 'retired'):
        raise ReleaseError(f"{p['name']} is frozen; new releases are disabled")
    if not SEMVER.fullmatch(m['version']):
        raise ReleaseError('Use MAJOR.MINOR.PATCH, optionally -alpha.N, -beta.N, -rc.N or -private.N')
    if m.get('edition', 'standard') not in p.get('editions', ['standard']):
        raise ReleaseError('Unknown release edition')
    if not m.get('targets') or len({t['id'] for t in m['targets']}) != len(m['targets']):
        raise ReleaseError('Release targets must be nonempty and unique')
    known = {t['id']: t for t in p['targets']}
    for target in m['targets']:
        if target['id'] not in known or any(target[k] != known[target['id']][k] for k in ('loader', 'minecraft', 'java')):
            raise ReleaseError(f"Unregistered or mismatched target: {target['id']}")
        if not re.fullmatch(r'[A-Za-z0-9.]+', target['minecraft']):
            raise ReleaseError('Unsafe Minecraft version')
    if not SAFE.fullmatch(m['artifact_id']):
        raise ReleaseError('Unsafe artifact ID')
    if m['artifact_id'] != p['artifact_id']:
        raise ReleaseError('Artifact ID must match the project registry')
    if not m.get('changelog') or Path(m['changelog']).is_absolute() or '..' in Path(m['changelog']).parts:
        raise ReleaseError('Changelog must be a relative path inside the release folder')
    return m, p


def channel(version):
    suffix = SEMVER.fullmatch(version).group(4)
    return 'release' if suffix is None else 'beta' if suffix in ('beta', 'rc') else 'alpha' if suffix == 'alpha' else 'private'


def filename(m, target):
    edition = '' if m.get('edition', 'standard') == 'standard' else '-' + m['edition']
    return f"{m['artifact_id']}-{m['version']}{edition}-{target['loader']}-{target['minecraft']}.jar"


def verify_changelog(path, version):
    text = Path(path).read_text(encoding='utf-8')
    headings = list(re.finditer(r'^#{1,3}\s+(?:\[)?(\d+\.\d+\.\d+(?:-(?:alpha|beta|rc|private)\.[1-9]\d*)?)(?:\])?(?:\s|$)', text, re.M))
    if not headings or headings[0].group(1) != version:
        raise ReleaseError('First versioned changelog heading must equal the release version')
    body = text[headings[0].end():headings[1].start() if len(headings) > 1 else len(text)].strip()
    if len(body) < 15 or re.search(r'\b(?:coming soon|todo|tbd|placeholder)\b', body, re.I):
        raise ReleaseError('Release changelog is missing or contains a placeholder')


def verify(m, p, target_id, jar_path, expected_commit=None):
    target = next((t for t in m['targets'] if t['id'] == target_id), None)
    if target is None:
        raise ReleaseError('Target is not part of this release')
    artifact = inspect_jar(jar_path)
    own = [x for x in artifact['mods'] if x['id'] in p['mod_ids']]
    if len(own) != 1 or own[0]['version'] != m['version']:
        raise ReleaseError(f"Embedded version must equal {m['version']}; renaming a JAR cannot change its version")
    if artifact['loader'] != target['loader']:
        raise ReleaseError('JAR loader does not match the release target')
    if artifact['max_class_major'] > target['java'] + 44:
        raise ReleaseError('JAR bytecode requires a newer Java version than the release target')
    marker = artifact['marker']
    expected = {'project': m['project'], 'version': m['version'], 'edition': m.get('edition', 'standard'),
                'target': target['id'], 'minecraft': target['minecraft'], 'loader': target['loader'], 'java': target['java']}
    if not isinstance(marker, dict) or any(marker.get(k) != v for k, v in expected.items()):
        raise ReleaseError('Missing or mismatched embedded release identity; rebuild with release.gradle')
    commit = marker.get('source_commit', '')
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ReleaseError('Release identity must include the source commit')
    if expected_commit and commit != expected_commit:
        raise ReleaseError('Built source commit does not match the expected commit')
    if marker.get('source_dirty') is not False:
        raise ReleaseError('Release was built from an uncommitted source tree')
    for previous in p.get('artifacts', []):
        # Same release identity must never acquire changed bytes, even after a rename.
        if previous['role'] == 'published' and previous['target'] == target_id and previous.get('edition', 'standard') == m.get('edition', 'standard'):
            if m['version'] in (previous['public_version'], previous['internal_version']) and previous['sha256'] != artifact['sha256']:
                raise ReleaseError('This version already identifies a published file; choose a new release version')
    return {'target': target_id, 'filename': filename(m, target), 'version': m['version'],
            'sha256': artifact['sha256'], 'payload_sha256': artifact['payload_sha256'], 'source_commit': commit,
            'minecraft': target['minecraft'], 'loader': target['loader'], 'java': target['java'],
            'curseforge_title': filename(m, target), 'curseforge_type': channel(m['version'])}


def prepare(registry, release_path, inputs, output, expected_commit=None):
    m, p = load_release(release_path, registry)
    verify_changelog(Path(release_path).parent / m['changelog'], m['version'])
    files = []
    seen = set()
    for path in inputs:
        artifact = inspect_jar(path)
        target = (artifact.get('marker') or {}).get('target')
        if target in seen:
            raise ReleaseError(f'Duplicate target: {target}')
        files.append((Path(path), verify(m, p, target, path, expected_commit)))
        seen.add(target)
    if seen != {t['id'] for t in m['targets']}:
        raise ReleaseError('Missing release targets; explicitly list only supported targets in release.json')
    out = Path(output)
    if out.exists() and any(out.iterdir()):
        raise ReleaseError('Output folder must be empty; existing release files will not be overwritten')
    out.mkdir(parents=True, exist_ok=True)
    edition = m.get('edition', 'standard')
    receipt = {'schema': 1, 'project': m['project'], 'version': m['version'], 'edition': edition,
               'git_tag': ('' if edition == 'standard' else edition + '/') + 'v' + m['version'],
               'files': [r for _, r in files]}
    for path, result in files:
        shutil.copyfile(path, out / result['filename'])
    shutil.copyfile(Path(release_path).parent / m['changelog'], out / 'CHANGELOG.md')
    dump(out / 'release.json', receipt)
    (out / 'SHA256SUMS').write_text(''.join(f"{r['sha256']}  {r['filename']}\n" for _, r in files), encoding='utf-8')
    return receipt


def register_published(registry, registry_path, release_path, jar_path, target_id, file_id, title):
    m, p = load_release(release_path, registry)
    if channel(m['version']) == 'private':
        raise ReleaseError('Private previews cannot be registered as public releases')
    verify_changelog(Path(release_path).parent / m['changelog'], m['version'])
    receipt = verify(m, p, target_id, jar_path)
    if title != receipt['filename'] or Path(jar_path).name != receipt['filename']:
        raise ReleaseError('Filename, CurseForge title and canonical release filename must match')
    cf = p.get('curseforge')
    if not cf:
        raise ReleaseError('Register the verified CurseForge project ID and slug in the registry first')
    if file_id <= 0:
        raise ReleaseError('Invalid CurseForge file ID')
    artifact = f"{cf['slug']}-{cf['project_id']}"
    url = f'https://www.cursemaven.com/curse/maven/{artifact}/{file_id}/{artifact}-{file_id}.jar'
    with urllib.request.urlopen(url, timeout=45) as response:
        published = response.read()
    if sha(published) != receipt['sha256']:
        raise ReleaseError('The public CurseForge artifact differs from the prepared JAR')
    if any(a.get('file_id') == file_id for a in p['artifacts']):
        raise ReleaseError('This published file ID is already recorded; history is append-only')
    record = {'role': 'published', 'target': target_id, 'edition': m.get('edition', 'standard'),
              'mod_id': next(x['id'] for x in inspect_jar(jar_path)['mods'] if x['id'] in p['mod_ids']),
              'internal_version': m['version'], 'public_version': m['version'],
              'filename': receipt['filename'], 'display_name': title,
              'sha256': receipt['sha256'], 'payload_sha256': receipt['payload_sha256'],
              'file_id': file_id, 'project_id': cf['project_id'],
              'source': f"https://www.curseforge.com/minecraft/mc-mods/{cf['slug']}/files/{file_id}",
              'evidence': 'Verified public artifact bytes against prepared release',
              'source_commit': receipt['source_commit']}
    p['artifacts'].append(record)
    check_registry(registry)
    tmp = Path(registry_path).with_suffix('.json.tmp')
    if tmp.exists():
        raise ReleaseError('A registry write is already pending')
    dump(tmp, registry)
    tmp.replace(registry_path)
    return record


def record_build(registry, registry_path, release_path, jar_path, target_id):
    m, p = load_release(release_path, registry)
    verify_changelog(Path(release_path).parent / m['changelog'], m['version'])
    result = verify(m, p, target_id, jar_path)
    record = {'role': 'development', 'target': target_id, 'edition': m.get('edition', 'standard'),
              'mod_id': next(x['id'] for x in inspect_jar(jar_path)['mods'] if x['id'] in p['mod_ids']),
              'internal_version': m['version'], 'sha256': result['sha256'],
              'payload_sha256': result['payload_sha256'], 'filename': result['filename'],
              'source_commit': result['source_commit'], 'evidence': 'Verified development artifact and release identity'}
    p['artifacts'].append(record)
    p['development_version'] = m['version']
    check_registry(registry)
    tmp = Path(registry_path).with_suffix('.json.tmp')
    if tmp.exists():
        raise ReleaseError('A registry write is already pending')
    dump(tmp, registry)
    tmp.replace(registry_path)
    return record


def report(registry):
    lines = ['| Mod | Published label → internal | Development reference | Matching |', '| --- | --- | --- | --- |']
    for p in registry['projects']:
        latest = {'published': {}, 'development': {}}
        for a in p.get('artifacts', []):
            latest[a['role']][(a['target'], a.get('edition', 'standard'))] = a
        pub = list(latest['published'].values())
        dev = list(latest['development'].values())
        labels = sorted({a['public_version'] + ' → ' + a['internal_version'] for a in pub})
        if p['status'] in ('deprecated', 'retired'):
            state = p['status'].capitalize() + ' — frozen'
        elif not pub:
            state = 'No public artifact recorded'
        elif not dev:
            state = 'Development artifact not recorded'
        elif not any(d.get('edition', 'standard') == a.get('edition', 'standard') for d in dev for a in pub):
            state = 'Separate editions — review independently'
        elif all(any(d['target'] == a['target'] and d.get('edition', 'standard') == a.get('edition', 'standard') and d['sha256'] == a['sha256'] for d in dev) for a in pub):
            state = 'Exact match'
        elif all(any(d['target'] == a['target'] and d.get('edition', 'standard') == a.get('edition', 'standard') and d['payload_sha256'] == a['payload_sha256'] for d in dev) for a in pub):
            state = 'Same entries — different ZIP packaging'
        else:
            state = 'Different builds — review changes'
        versions = sorted({a['internal_version'] for a in dev}) or [p.get('development_version') or 'Unverified']
        lines.append('| ' + ' | '.join([p['name'], '; '.join(labels) or '—', '; '.join(versions), state]) + ' |')
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, default=ROOT / 'registry.json')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check')
    listing = sub.add_parser('list'); listing.add_argument('--output', type=Path)
    inspect = sub.add_parser('inspect'); inspect.add_argument('jars', nargs='+', type=Path)
    match = sub.add_parser('match'); match.add_argument('jar', type=Path)
    new = sub.add_parser('new')
    new.add_argument('project'); new.add_argument('version'); new.add_argument('--edition', default='standard')
    new.add_argument('--out', type=Path, required=True); new.add_argument('--target', action='append')
    check = sub.add_parser('verify')
    check.add_argument('--release', type=Path, required=True); check.add_argument('--jar', type=Path, required=True)
    check.add_argument('--target', required=True); check.add_argument('--expected-commit')
    package = sub.add_parser('prepare')
    package.add_argument('--release', type=Path, required=True); package.add_argument('--jar', action='append', type=Path, required=True)
    package.add_argument('--out', type=Path, required=True); package.add_argument('--expected-commit')
    register = sub.add_parser('register')
    register.add_argument('--release', type=Path, required=True); register.add_argument('--jar', type=Path, required=True)
    register.add_argument('--target', required=True); register.add_argument('--file-id', type=int, required=True)
    register.add_argument('--title', required=True)
    record = sub.add_parser('record-build')
    record.add_argument('--release', type=Path, required=True); record.add_argument('--jar', type=Path, required=True)
    record.add_argument('--target', required=True)
    args = parser.parse_args(argv)
    try:
        registry = read_json(args.registry)
        check_registry(registry)
        if args.command == 'check':
            result = {'ok': True, 'projects': len(registry['projects'])}
        elif args.command == 'list':
            text = report(registry)
            if args.output:
                args.output.write_text(text, encoding='utf-8')
            else:
                print(text, end='')
            return 0
        elif args.command == 'inspect':
            result = [inspect_jar(path) for path in args.jars]
        elif args.command == 'match':
            result = match_jar(registry, args.jar)
        elif args.command == 'new':
            if args.out.exists():
                raise ReleaseError('Release file already exists; edit its version deliberately')
            p = project(registry, args.project)
            targets = p.get('targets', [])
            if args.target:
                if set(args.target) - {t['id'] for t in targets}:
                    raise ReleaseError('Unknown target')
                targets = [t for t in targets if t['id'] in args.target]
            result = {'schema': 1, 'project': p['id'], 'artifact_id': p['artifact_id'], 'version': args.version,
                      'edition': args.edition, 'changelog': 'CHANGELOG.md', 'targets': targets}
            args.out.parent.mkdir(parents=True, exist_ok=True)
            dump(args.out, result)
            try:
                load_release(args.out, registry)
            except Exception:
                args.out.unlink()
                raise
        elif args.command == 'verify':
            m, p = load_release(args.release, registry)
            verify_changelog(args.release.parent / m['changelog'], m['version'])
            result = verify(m, p, args.target, args.jar, args.expected_commit)
        elif args.command == 'register':
            result = register_published(registry, args.registry, args.release, args.jar, args.target, args.file_id, args.title)
        elif args.command == 'record-build':
            result = record_build(registry, args.registry, args.release, args.jar, args.target)
        else:
            result = prepare(registry, args.release, args.jar, args.out, args.expected_commit)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ReleaseError, OSError, KeyError, TypeError, zipfile.BadZipFile, tomllib.TOMLDecodeError, json.JSONDecodeError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
