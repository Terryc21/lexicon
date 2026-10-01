"""Synthetic regression coverage. Never reads a user's home or installed skills."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/lexicon/scripts/lexicon.py'
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('lexicon', SCRIPT)
lx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lx)


def entry(term, **fields):
    return dict(term=term, definition='An independent concept.', example='A simple example of the concept.',
                use_case='Use it to explain a concept.', **fields)


class Integrity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lexicon test ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root/'home'; (self.home/'lists').mkdir(parents=True)
        self.vocab = self.root/'vocabulary.yaml'
        self.vocab.write_text('- term: Existing\n  definition: Original text\n')
        self.config = self.home/'config.yaml'
        self.config.write_text(yaml.safe_dump(dict(schema_version=1, tutorial_creator_vocab=str(self.vocab))))
        self.call('new-list','--name','swift')

    def call(self,*argv,payload=None,code=0):
        buf=io.StringIO()
        with patch.object(sys,'argv',['lexicon','--home',str(self.home),*map(str,argv)]), \
             patch.object(sys,'stdin',io.StringIO(json.dumps(payload) if payload is not None else '')), \
             contextlib.redirect_stdout(buf):
            try:
                lx.main()
                actual=0
            except SystemExit as ex:
                actual=ex.code
        self.assertEqual(actual,code,buf.getvalue())
        return json.loads(buf.getvalue())

    def data(self,slug='swift'):
        return yaml.safe_load((self.home/'lists'/f'{slug}.yaml').read_text())

    def add(self,entries,slug='swift'):
        return self.call('add',slug,'--entries','-',payload=entries)

    def snapshot(self):
        return {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def assert_rejected_unchanged(self,*args,payload=None):
        before=self.snapshot()
        result=self.call(*args,payload=payload,code=1)
        self.assertFalse(result['ok'])
        self.assertEqual(self.snapshot(),before)
        return result

    def test_invalid_add_and_batch_leave_both_views_unchanged(self):
        self.add(entry('@MainActor'))
        for bad in (entry('Beta',aliases=[42]),entry('Beta',seen_in=[None]),entry('Beta',first_encountered='bad'),entry('Beta',notes=[])):
            with self.subTest(bad=bad):
                self.assert_rejected_unchanged('add','swift','--entries','-',payload=[entry('Alpha'),bad])
        self.assert_rejected_unchanged('add','swift','--entries','-',payload=[entry('Alpha'),entry('MainActor')])

    def test_merge_invalid_override_is_atomic(self):
        self.add([entry('Alpha'),entry('Beta')])
        self.assert_rejected_unchanged('merge','swift','Alpha','Beta','--override','-',payload={'definition':''})

    def test_merge_retains_provenance_and_rewrites_relations(self):
        self.add([entry('Alpha'),entry('Beta'),entry('Gamma',related_terms=['Beta'])])
        self.call('merge','swift','Alpha','Beta')
        data=self.data()['entries']
        self.assertEqual(data[0]['aliases'],['Beta'])
        self.assertTrue(data[0]['seen_in'])
        self.assertEqual(data[1]['related_terms'],['Alpha'])
        self.assertTrue(self.call('validate','--all')['ok'])

    def test_scope_failure_is_atomic(self):
        self.assert_rejected_unchanged('set-scope','swift','--scope','-',payload={'include':42})

    def test_slugs_and_symlink_escape(self):
        for slug in ('../../outside',str(self.root/'outside'),'UPPER','two--hyphens'):
            self.assert_rejected_unchanged('new-list','--name','Escape','--slug',slug)
        external=self.root/'external.yaml'; external.write_text('unchanged')
        (self.home/'lists/escape.yaml').symlink_to(external)
        self.assert_rejected_unchanged('show','escape')
        self.assertEqual(external.read_text(),'unchanged')

    def test_stored_slug_mismatch(self):
        d=self.data();d['slug']='../../outside'
        (self.home/'lists/swift.yaml').write_text(yaml.safe_dump(d))
        self.assert_rejected_unchanged('add','swift','--entries','-',payload=entry('Alpha'))

    def test_future_schema_all_list_writers_refuse(self):
        self.call('new-list','--name','other')
        d=self.data();d['schema_version']=2
        (self.home/'lists/swift.yaml').write_text(yaml.safe_dump(d))
        cases=[(('add','swift','--entries','-'),entry('Alpha')),
               (('update','swift','--entries','-'),{'term':'Alpha','definition':'new'}),
               (('remove','swift','Alpha'),None),(('merge','swift','Alpha','Beta'),None),
               (('move','swift','Alpha','--to','other'),None),(('undo','swift'),None),
               (('render','swift'),None),(('set-scope','swift','--scope','-'),{'level':'beginner'}),
               (('export-tc','swift','--all','--vocab',self.vocab),None)]
        for args,payload in cases:
            with self.subTest(command=args[0]):self.assert_rejected_unchanged(*args,payload=payload)

    def test_future_config_refused(self):
        self.config.write_text('schema_version: 2\n')
        for args in [('config','--default-list','swift'),('new-list','--name','other'),('init',)]:
            self.assert_rejected_unchanged(*args)

    def test_validate_exit_status_and_malformed_elements(self):
        d=self.data(); d['entries']=[entry('Bad',aliases=[42])]
        (self.home/'lists/swift.yaml').write_text(yaml.safe_dump(d))
        result=self.call('validate','--all',code=1)
        self.assertFalse(result['ok'])
        r=subprocess.run([sys.executable,'-B',str(SCRIPT),'--home',str(self.home),'validate','--all'],capture_output=True,text=True)
        self.assertEqual(r.returncode,1);self.assertFalse(json.loads(r.stdout)['ok'])

    def test_rename_or_alias_collision_rejected(self):
        self.add([entry('Alpha'),entry('Beta')])
        for fields in ({'rename_to':'Beta'},{'aliases':['Beta']},{'rename_to':'Betas'}):
            self.assert_rejected_unchanged('update','swift','--entries','-',payload={'term':'Alpha',**fields})

    def test_confirmed_distinct_supported_but_ambiguous_lookup_refused(self):
        self.add(entry('Unit testing'))
        self.add(entry('Unit testing (isolation sense)',confirmed_distinct=True))
        self.assertTrue(self.call('validate','swift')['ok'])
        self.assertEqual(self.call('audit','swift')['within_list'],[])
        self.assert_rejected_unchanged('remove','swift','unit testings')

    def test_rename_rewrites_related_and_distinct_references(self):
        self.add([entry('Alpha'),entry('Beta',related_terms=['Alpha'],distinct_from=['Alpha'])])
        self.call('update','swift','--entries','-',payload={'term':'Alpha','rename_to':'Gamma'})
        beta=next(e for e in self.data()['entries'] if e['term']=='Beta')
        self.assertEqual(beta['related_terms'],['Gamma']);self.assertEqual(beta['distinct_from'],['Gamma'])
        self.assertTrue(self.call('validate','swift')['ok'])

    def test_copy_repeated_selection_once_and_preserves_source(self):
        self.call('new-list','--name','other');self.add(entry('Alpha',aliases=['A']))
        old=(self.home/'lists/swift.yaml').read_bytes()
        self.call('move','swift','Alpha','A','Alpha','--to','other','--copy')
        self.assertEqual(len(self.data('other')['entries']),1)
        self.assertEqual((self.home/'lists/swift.yaml').read_bytes(),old)

    def test_move_repairs_relationships_on_both_sides(self):
        self.call('new-list','--name','other')
        self.add([entry('Alpha',related_terms=['Beta']),entry('Beta',related_terms=['Alpha'])])
        result=self.call('move','swift','Alpha','--to','other')
        self.assertEqual(len(result['removed_relationships']),2)
        self.assertTrue(self.call('validate','--all')['ok'])

    def test_move_together_keeps_relationships(self):
        self.call('new-list','--name','other')
        self.add([entry('Alpha',related_terms=['Beta']),entry('Beta',related_terms=['Alpha'])])
        result=self.call('move','swift','Alpha','Beta','--to','other')
        self.assertEqual(result['removed_relationships'],[])
        self.assertEqual(self.data('other')['entries'][0]['related_terms'],['Beta'])

    def test_move_rollback_on_source_failure(self):
        self.call('new-list','--name','other');self.add(entry('Alpha'))
        before=self.snapshot();original=lx.write_bytes_atomic;failed=False
        def fail_once(path,content,**kwargs):
            nonlocal failed
            if path==str(self.home/'lists/swift.yaml') and not failed:
                failed=True;raise OSError('injected source write failure')
            return original(path,content,**kwargs)
        with patch.object(lx,'write_bytes_atomic',side_effect=fail_once):
            self.call('move','swift','Alpha','--to','other',code=1)
        self.assertTrue(failed);self.assertEqual(self.snapshot(),before)

    def test_render_failure_before_commit_and_view_write_failure_roll_back(self):
        before=self.snapshot()
        with patch.object(lx,'render_md',side_effect=ValueError('injected renderer failure')):
            self.call('add','swift','--entries','-',payload=entry('Alpha'),code=1)
        self.assertEqual(self.snapshot(),before)
        original=lx.write_bytes_atomic;failed=False
        def fail_once(path,content,**kwargs):
            nonlocal failed
            if path==str(self.home/'lists/swift.md') and not failed:
                failed=True;raise OSError('injected view write failure')
            return original(path,content,**kwargs)
        with patch.object(lx,'write_bytes_atomic',side_effect=fail_once):
            self.call('add','swift','--entries','-',payload=entry('Alpha'),code=1)
        self.assertTrue(failed);self.assertEqual(self.snapshot(),before)

    def test_undo_restores_bytes_and_consumes_one_backup(self):
        self.add(entry('Alpha'));old=(self.home/'lists/swift.yaml').read_bytes()
        self.call('update','swift','--entries','-',payload={'term':'Alpha','definition':'Updated.'})
        count=len(list((self.home/'.backups').glob('*.yaml')))
        self.call('undo','swift')
        self.assertEqual((self.home/'lists/swift.yaml').read_bytes(),old)
        self.assertEqual(len(list((self.home/'.backups').glob('*.yaml'))),count-1)

    def test_backup_retention_and_order(self):
        self.add(entry('Alpha'))
        for i in range(23): self.call('update','swift','--entries','-',payload={'term':'Alpha','definition':f'Definition {i}'})
        self.assertEqual(len(list((self.home/'.backups').glob('*.yaml'))),20)
        self.call('undo','swift');self.assertEqual(self.data()['entries'][0]['definition'],'Definition 21')

    def test_export_preserves_bytes_undo_marker_and_readonly_import(self):
        self.add(entry('Alpha'));old=self.vocab.read_bytes()
        result=self.call('export-tc','swift','--all','--vocab',self.vocab)
        self.assertTrue(self.vocab.read_bytes().startswith(old))
        self.assertEqual(Path(result['undo_marker']).read_text(),'Alpha\n')
        self.assertTrue(self.data()['entries'][0]['exported_to_tc'])
        before=self.snapshot();self.call('import-tc','--vocab',self.vocab);self.assertEqual(self.snapshot(),before)
        before=self.snapshot();self.call('export-tc','swift','--all','--vocab',self.vocab);self.assertEqual(self.snapshot(),before)

    def test_export_empty_and_commented_flow_sequence(self):
        self.add(entry('Alpha'))
        for i,raw in enumerate(('[]\n','# Empty glossary\n[] # no entries\n','', '# empty\n')):
            path=self.root/f'empty-{i}.yaml';path.write_text(raw)
            self.call('export-tc','swift','--all','--vocab',path)
            self.assertEqual(yaml.safe_load(path.read_text())[0]['term'],'Alpha')
            if 'Empty glossary' in raw:self.assertIn('# Empty glossary',path.read_text())

    def test_nonempty_flow_export_rejected_without_mutation(self):
        self.add(entry('Alpha'));self.vocab.write_text('[{term: Existing}]\n')
        self.assert_rejected_unchanged('export-tc','swift','--all','--vocab',self.vocab)

    def test_export_marker_collision_rejected_before_mutation(self):
        self.add([entry('Alpha'),entry('Beta')])
        with patch.object(lx,'stamp',return_value='2026-09-30T12-00-00'):
            result=self.call('export-tc','swift','Alpha','--vocab',self.vocab)
            self.assert_rejected_unchanged('export-tc','swift','Beta','--vocab',self.vocab)
            self.assertEqual(Path(result['undo_marker']).read_text(),'Alpha\n')
        with patch.object(lx,'stamp',return_value='2026-09-30T12-00-01'):
            self.call('export-tc','swift','Beta','--vocab',self.vocab)
        self.assertEqual(len(list(self.root.glob('vocabulary.yaml.add-*'))),2)

    def test_export_rollback_on_marker_and_source_failures(self):
        self.add(entry('Alpha'))
        for failure in ('marker','source'):
            before=self.snapshot();original=lx.write_bytes_atomic;failed=False
            def fail_once(path,content,**kwargs):
                nonlocal failed
                target=(str(self.vocab)+'.add-') in path if failure=='marker' else path==str(self.home/'lists/swift.yaml')
                if target and not failed:
                    failed=True;raise OSError('injected '+failure+' failure')
                return original(path,content,**kwargs)
            with patch.object(lx,'write_bytes_atomic',side_effect=fail_once):
                self.call('export-tc','swift','--all','--vocab',self.vocab,code=1)
            self.assertTrue(failed);self.assertEqual(self.snapshot(),before)

    def test_rollback_failure_retains_recovery_and_blocks_new_writes(self):
        self.add(entry('Alpha'));original=lx.write_bytes_atomic;old=self.vocab.read_bytes()
        def fail_twice(path,content,**kwargs):
            if path.startswith(str(self.vocab)+'.add-') or (path==str(self.vocab) and content==old):
                raise OSError('injected persistence failure')
            return original(path,content,**kwargs)
        with patch.object(lx,'write_bytes_atomic',side_effect=fail_twice):
            result=self.call('export-tc','swift','--all','--vocab',self.vocab,code=1)
        self.assertIn('recovery copies retained',result['error'])
        self.assertTrue(list((self.home/'.recovery').glob('*/manifest.json')))
        self.assert_rejected_unchanged('add','swift','--entries','-',payload=entry('Beta'))

    def test_external_change_is_not_overwritten_during_rollback(self):
        self.add(entry('Alpha'));original=lx.write_bytes_atomic
        def fail_and_change(path,content,**kwargs):
            if path.startswith(str(self.vocab)+'.add-'):
                self.vocab.write_text('- term: External writer\n')
                raise OSError('injected concurrent writer')
            return original(path,content,**kwargs)
        with patch.object(lx,'write_bytes_atomic',side_effect=fail_and_change):
            result=self.call('export-tc','swift','--all','--vocab',self.vocab,code=1)
        self.assertEqual(self.vocab.read_text(),'- term: External writer\n')
        self.assertIn('recovery copies retained',result['error'])

    def test_preexisting_lock_refuses_write(self):
        (self.home/'.write-lock').mkdir()
        self.assert_rejected_unchanged('add','swift','--entries','-',payload=entry('Alpha'))

    def test_new_list_config_failure_rolls_back_created_files(self):
        cfg=yaml.safe_load(self.config.read_text());cfg['default_list']=None
        self.config.write_text(yaml.safe_dump(cfg))
        before=self.snapshot();original=lx.write_bytes_atomic;failed=False
        def fail_once(path,content,**kwargs):
            nonlocal failed
            if path==str(self.config) and not failed:
                failed=True;raise OSError('injected config failure')
            return original(path,content,**kwargs)
        with patch.object(lx,'write_bytes_atomic',side_effect=fail_once):
            self.call('new-list','--name','other',code=1)
        self.assertTrue(failed);self.assertEqual(self.snapshot(),before)

    def test_new_file_publication_cannot_overwrite_racing_marker(self):
        self.add(entry('Alpha'));old=self.vocab.read_bytes();original=lx.write_bytes_atomic
        collided=[]
        def race(path,content,**kwargs):
            if path.startswith(str(self.vocab)+'.add-'):
                Path(path).write_text('External marker\n');collided.append(path)
            return original(path,content,**kwargs)
        with patch.object(lx,'write_bytes_atomic',side_effect=race):
            self.call('export-tc','swift','--all','--vocab',self.vocab,code=1)
        self.assertEqual(self.vocab.read_bytes(),old)
        self.assertEqual(Path(collided[0]).read_text(),'External marker\n')
        self.assertIsNone(self.data()['entries'][0]['exported_to_tc'])

    def test_move_preserves_confirmed_normalized_distinctions(self):
        self.call('new-list','--name','other')
        self.add(entry('Unit testing'))
        self.add(entry('Unit testing (isolation sense)',distinct_from=['Unit testing']))
        result=self.call('move','swift','Unit testing','Unit testing (isolation sense)','--to','other')
        self.assertEqual(len(result['moved']),2)
        self.assertTrue(self.call('validate','--all')['ok'])

    def test_merge_preserves_distinction_references(self):
        self.add([entry('Alpha'),entry('Beta',distinct_from=['Gamma']),entry('Gamma',distinct_from=['Beta'])])
        self.call('merge','swift','Alpha','Beta')
        alpha,gamma=self.data()['entries']
        self.assertEqual(alpha['distinct_from'],['Gamma'])
        self.assertEqual(gamma['distinct_from'],['Alpha'])

    def test_export_dry_run_validates_serialization_without_writes(self):
        self.add(entry('Alpha'));before=self.snapshot()
        self.call('export-tc','swift','--all','--vocab',self.vocab,'--dry-run')
        self.assertEqual(self.snapshot(),before)
        self.vocab.write_text('[{term: Existing}]\n')
        self.assert_rejected_unchanged('export-tc','swift','--all','--vocab',self.vocab,'--dry-run')

    def test_undo_view_failure_retains_current_and_backup(self):
        self.add(entry('Alpha'));before=self.snapshot();original=lx.write_bytes_atomic;failed=False
        def fail_once(path,content,**kwargs):
            nonlocal failed
            if path==str(self.home/'lists/swift.md') and not failed:
                failed=True;raise OSError('injected undo view failure')
            return original(path,content,**kwargs)
        with patch.object(lx,'write_bytes_atomic',side_effect=fail_once):
            self.call('undo','swift',code=1)
        self.assertTrue(failed);self.assertEqual(self.snapshot(),before)

    def test_source_helpers_synthetic_only(self):
        html=self.root/'page.html';html.write_text('<head><title>Test</title></head><p>Learn actors</p><script>secret()</script>')
        result=self.call('html-text',html)
        self.assertEqual(result['title'],'Test');self.assertIn('Learn actors',result['text']);self.assertNotIn('secret',result['text'])
        transcript=self.root/'session.jsonl';transcript.write_text(json.dumps({'type':'user','timestamp':'2026-09-20T12:00:00Z','message':{'content':'What is isolation?'}})+'\n')
        result=self.call('session-text',transcript,'--max-chars','10')
        self.assertTrue(result['truncated']);self.assertEqual(result['date'],'2026-09-20')

    def test_generated_view_uses_protected_edit_guidance(self):
        text=(self.home/'lists/swift.md').read_text()
        self.assertIn('Ask lexicon',text);self.assertNotIn('Edit the YAML',text)

    def test_html_view_is_written_escaped_searchable_and_follows_undo(self):
        self.add([entry('@MainActor',aliases=['MainActor'],type='attribute',related_terms=['Sendable']),
                  {**entry('Sendable'),'definition':'Safe to pass <across> actors & tasks.','example':'let x = 1 < 2'}])
        page=(self.home/'lists/swift.html').read_text()
        for needle in ('<!doctype html>','id="q"','class="letters"','id="theme"','data-t="attribute"','href="#sendable"','<script>'):
            self.assertIn(needle,page)
        self.assertIn('Safe to pass &lt;across&gt; actors &amp; tasks.',page)
        self.assertNotIn('<across>',page)
        self.assertNotIn('src="http',page);self.assertNotIn('<link',page)
        self.call('undo','swift')
        self.assertNotIn('Sendable',(self.home/'lists/swift.html').read_text())
        self.call('render','swift')
        self.assertTrue((self.home/'lists/swift.html').exists())

if __name__=='__main__': unittest.main()
