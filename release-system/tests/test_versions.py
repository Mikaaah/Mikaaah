import copy
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tnk_versions as v


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.registry = v.read_json(v.ROOT / 'registry.json')
        self.p = v.project(self.registry, 'flow-control')
        self.nf = next(t for t in self.p['targets'] if t['id'] == 'neoforge-1.21.1')
        self.m = {'schema': 1, 'project': 'flow-control', 'artifact_id': 'flow-control',
                  'version': '9.9.9', 'edition': 'standard', 'changelog': 'CHANGELOG.md', 'targets': [self.nf]}
        self.release = self.root / 'release.json'
        v.dump(self.release, self.m)
        (self.root / 'CHANGELOG.md').write_text('## 9.9.9\n\n- Corrected controller synchronization after a server restart.\n')

    def tearDown(self):
        self.tmp.cleanup()

    def jar(self, name='input.jar', loader='neoforge', version='9.9.9', marker=True, changes=None, payload=b'implementation'):
        f = self.root / name
        identity = {'project': 'flow-control', 'version': version, 'edition': 'standard',
                    'target': loader + '-1.21.1', 'minecraft': '1.21.1', 'loader': loader,
                    'source_commit': 'a' * 40, 'source_dirty': False, 'java': 21}
        identity.update(changes or {})
        with zipfile.ZipFile(f, 'w') as z:
            if loader == 'fabric':
                z.writestr('fabric.mod.json', json.dumps({'id': 'streamcontrol', 'version': version}))
            else:
                path = 'META-INF/neoforge.mods.toml' if loader == 'neoforge' else 'META-INF/mods.toml'
                z.writestr(path, f'[[mods]]\nmodId="streamcontrol"\nversion="{version}"\n')
            z.writestr('example/Controller.class', payload)
            if marker:
                z.writestr(v.MARKER, json.dumps(identity))
        return f

    def test_registry_contains_all_projects_and_frozen_exceptions(self):
        self.assertEqual(v.check_registry(self.registry), 14)
        self.assertEqual({p['id'] for p in self.registry['projects'] if p['status'] == 'deprecated'}, {'utf-streams', 'utf-alexs-caves'})

    def test_each_flow_port_matches_its_published_bytes(self):
        published = [a for a in self.p['artifacts'] if a['role'] == 'published']
        self.assertEqual(len(published), 8)
        for a in published:
            dev = next(d for d in self.p['artifacts'] if d['role'] == 'development' and d['target'] == a['target'])
            self.assertEqual(a['sha256'], dev['sha256'])
            self.assertEqual(a['public_version'], '0.2.0')
            self.assertEqual(a['internal_version'], '0.2.2')

    def test_laser_legacy_title_matches_latest_bytes(self):
        p = v.project(self.registry, 'alchemical-lasers')
        self.assertEqual(p['artifacts'][0]['sha256'], p['artifacts'][1]['sha256'])

    def test_all_loader_metadata_formats(self):
        for loader in ('neoforge', 'forge', 'fabric'):
            a = v.inspect_jar(self.jar(name=loader+'.jar', loader=loader))
            self.assertEqual(a['loader'], loader)
            self.assertEqual(a['mods'][0]['version'], '9.9.9')

    def test_renamed_historical_jar_is_same_build(self):
        jar = self.jar(marker=False)
        a = v.inspect_jar(jar)
        self.p['artifacts'].append({'role': 'development', 'target': self.nf['id'],
                                   'internal_version': '9.9.9', 'sha256': a['sha256'], 'payload_sha256': a['payload_sha256']})
        renamed = self.root / 'misleading-1.0.jar'
        renamed.write_bytes(jar.read_bytes())
        self.assertEqual(v.match_jar(self.registry, renamed)['status'], 'exact-known-build')

    def test_repacked_jar_is_recognized_without_claiming_identical_bytes(self):
        jar = self.jar(marker=False)
        a = v.inspect_jar(jar)
        self.p['artifacts'].append({'role': 'development', 'target': self.nf['id'],
                                   'internal_version': '9.9.9', 'sha256': a['sha256'], 'payload_sha256': a['payload_sha256']})
        repacked = self.root / 'repacked.jar'
        with zipfile.ZipFile(jar) as old, zipfile.ZipFile(repacked, 'w', compression=zipfile.ZIP_DEFLATED) as new:
            for name in reversed(old.namelist()):
                new.writestr(name, old.read(name))
        self.assertEqual(v.match_jar(self.registry, repacked)['status'], 'repacked-known-build')

    def test_rename_cannot_pass_a_version_mismatch(self):
        with self.assertRaisesRegex(v.ReleaseError, 'Embedded version'):
            v.verify(self.m, self.p, self.nf['id'], self.jar(version='0.2.2'))

    def test_new_release_requires_marker(self):
        with self.assertRaisesRegex(v.ReleaseError, 'release identity'):
            v.verify(self.m, self.p, self.nf['id'], self.jar(marker=False))

    def test_wrong_minecraft_rejected_even_when_filename_is_correct(self):
        with self.assertRaisesRegex(v.ReleaseError, 'release identity'):
            v.verify(self.m, self.p, self.nf['id'], self.jar(changes={'minecraft': '1.21.11'}))

    def test_wrong_loader_rejected(self):
        with self.assertRaisesRegex(v.ReleaseError, 'loader'):
            v.verify(self.m, self.p, self.nf['id'], self.jar(loader='fabric'))

    def test_bytecode_newer_than_target_java_rejected(self):
        with self.assertRaisesRegex(v.ReleaseError, 'newer Java'):
            v.verify(self.m, self.p, self.nf['id'], self.jar(payload=b'\xca\xfe\xba\xbe\x00\x00\x00\x45'))

    def test_wrong_edition_rejected(self):
        with self.assertRaisesRegex(v.ReleaseError, 'release identity'):
            v.verify(self.m, self.p, self.nf['id'], self.jar(changes={'edition': 'tnk'}))

    def test_dirty_and_wrong_source_commit_rejected(self):
        with self.assertRaisesRegex(v.ReleaseError, 'uncommitted'):
            v.verify(self.m, self.p, self.nf['id'], self.jar(changes={'source_dirty': True}))
        with self.assertRaisesRegex(v.ReleaseError, 'expected commit'):
            v.verify(self.m, self.p, self.nf['id'], self.jar(), 'b'*40)

    def test_changed_bytes_cannot_reuse_a_published_version(self):
        self.p['artifacts'].append({'role': 'published', 'target': self.nf['id'], 'public_version': '9.9.9',
                                   'internal_version': '9.9.9', 'sha256': '0'*64})
        with self.assertRaisesRegex(v.ReleaseError, 'already identifies'):
            v.verify(self.m, self.p, self.nf['id'], self.jar())

    def test_changelog_placeholder_and_wrong_version_rejected(self):
        path = self.root / 'CHANGELOG.md'
        for text in ('## 9.9.9\n\nComing soon\n', '## 9.9.8\n\n- A meaningful release note is present.\n'):
            path.write_text(text)
            with self.assertRaises(v.ReleaseError):
                v.verify_changelog(path, '9.9.9')

    def test_private_build_not_promoted_to_stable(self):
        self.assertEqual(v.channel('1.5.0-private.1'), 'private')
        self.assertEqual(v.channel('1.0.0-rc.1'), 'beta')
        self.assertEqual(v.channel('1.0.0'), 'release')

    def test_frozen_project_releases_blocked(self):
        self.m.update(project='utf-streams', version='1.0.0')
        v.dump(self.release, self.m)
        with self.assertRaisesRegex(v.ReleaseError, 'frozen'):
            v.load_release(self.release, self.registry)

    def test_prepare_preserves_bytes_and_generates_matching_names(self):
        jar = self.jar(); out = self.root / 'out'
        receipt = v.prepare(self.registry, self.release, [jar], out)
        f = receipt['files'][0]
        self.assertEqual(f['filename'], 'flow-control-9.9.9-neoforge-1.21.1.jar')
        self.assertEqual(f['curseforge_title'], f['filename'])
        self.assertEqual((out/f['filename']).read_bytes(), jar.read_bytes())
        self.assertIn(f['sha256'], (out/'SHA256SUMS').read_text())

    def test_prepare_rejects_missing_port_and_duplicate_target(self):
        self.m['targets'].append(next(t for t in self.p['targets'] if t['id'] == 'fabric-1.21.1'))
        v.dump(self.release, self.m)
        with self.assertRaisesRegex(v.ReleaseError, 'Missing release targets'):
            v.prepare(self.registry, self.release, [self.jar()], self.root/'out')
        with self.assertRaisesRegex(v.ReleaseError, 'Duplicate target'):
            jar = self.jar(); v.prepare(self.registry, self.release, [jar, jar], self.root/'out')

    def test_multi_loader_release_uses_one_version(self):
        self.m['targets'].append(next(t for t in self.p['targets'] if t['id'] == 'fabric-1.21.1'))
        v.dump(self.release, self.m)
        receipt = v.prepare(self.registry, self.release,
                            [self.jar('nf.jar'), self.jar('fabric.jar', loader='fabric')], self.root/'out')
        self.assertEqual({f['version'] for f in receipt['files']}, {'9.9.9'})

    def test_existing_output_never_overwritten(self):
        jar=self.jar(); out=self.root/'out';out.mkdir();(out/'keep.txt').write_text('keep')
        with self.assertRaisesRegex(v.ReleaseError, 'empty'):
            v.prepare(self.registry, self.release, [jar], out)
        self.assertEqual((out/'keep.txt').read_text(), 'keep')

    def test_unidentified_loot_editions_are_not_called_outdated_by_number(self):
        self.assertIn('Separate editions', v.report(self.registry))

    def test_old_published_history_does_not_override_current_match(self):
        old = copy.deepcopy(next(a for a in self.p['artifacts'] if a['role'] == 'published'))
        old.update(sha256='0'*64, payload_sha256='1'*64, public_version='0.0.1', internal_version='0.0.1')
        self.p['artifacts'].insert(0, old)
        row = next(line for line in v.report(self.registry).splitlines() if line.startswith('| Flow Control'))
        self.assertIn('Exact match', row)
        self.assertNotIn('0.0.1', row)

    def test_development_record_uses_actual_verified_artifact(self):
        reg = self.root/'registry.json';v.dump(reg,self.registry)
        jar=self.jar()
        recorded=v.record_build(self.registry,reg,self.release,jar,self.nf['id'])
        self.assertEqual(recorded['sha256'],v.inspect_jar(jar)['sha256'])
        self.assertEqual(v.read_json(reg)['projects'][0]['development_version'],'9.9.9')

    def test_private_build_cannot_be_registered_publicly(self):
        self.m['version']='1.0.0-private.1';v.dump(self.release,self.m)
        with self.assertRaisesRegex(v.ReleaseError, 'Private previews'):
            v.register_published(self.registry,self.root/'registry.json',self.release,self.jar(),self.nf['id'],123,'input.jar')

    def test_public_registration_checks_downloaded_bytes_before_write(self):
        jar=self.jar('flow-control-9.9.9-neoforge-1.21.1.jar')
        self.p['curseforge']={'slug':'flow-control-streams-reflowing-addon','project_id':1750740}
        reg=self.root/'registry.json';v.dump(reg,self.registry)
        original=reg.read_bytes()
        with patch.object(v.urllib.request, 'urlopen', return_value=io.BytesIO(b'not the uploaded jar')):
            with self.assertRaisesRegex(v.ReleaseError, 'differs'):
                v.register_published(self.registry,reg,self.release,jar,self.nf['id'],123,jar.name)
        self.assertEqual(reg.read_bytes(),original)
        with patch.object(v.urllib.request,'urlopen',return_value=io.BytesIO(jar.read_bytes())):
            result=v.register_published(self.registry,reg,self.release,jar,self.nf['id'],123,jar.name)
        self.assertEqual(result['public_version'], '9.9.9')


if __name__ == '__main__':
    unittest.main()
