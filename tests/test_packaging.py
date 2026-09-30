import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import yaml

ROOT=Path(__file__).resolve().parents[1]
EVALS=ROOT/'skills/lexicon/evals'
SCRIPT=ROOT/'skills/lexicon/scripts/lexicon.py'
spec=importlib.util.spec_from_file_location('prepare_case',EVALS/'prepare.py')
prepare_case=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare_case)

class Packaging(unittest.TestCase):
    def test_all_cases_prepare_and_validate_with_explicit_isolation(self):
        with tempfile.TemporaryDirectory(prefix='lexicon cases ') as folder:
            for case in range(1,6):
                result=prepare_case.prepare(case,Path(folder)/str(case))
                data=json.loads((Path(result['cwd'])/'case.json').read_text())
                cfg=yaml.safe_load((Path(result['lexicon_home'])/'config.yaml').read_text())
                self.assertEqual(cfg['tutorial_creator_vocab'],result['vocabulary'])
                self.assertTrue(Path(result['vocabulary']).is_file())
                original=next(e for e in json.loads((EVALS/'evals.json').read_text())['evals'] if e['id']==case)
                for ref in original['files']: self.assertTrue((Path(result['cwd'])/ref).exists(),ref)
                r=subprocess.run([sys.executable,'-B',str(SCRIPT),'--home',result['lexicon_home'],'validate','--all'],capture_output=True,text=True)
                self.assertEqual(r.returncode,0,r.stdout+r.stderr)
                self.assertEqual(data['live_result'],'not run')

    def test_preparation_refuses_existing_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            sentinel=Path(folder)/'keep';sentinel.write_text('unchanged')
            with self.assertRaises(ValueError): prepare_case.prepare(1,folder)
            self.assertEqual(sentinel.read_text(),'unchanged')

    def test_first_list_shell_example_runs_with_apostrophe(self):
        skill=(ROOT/'skills/lexicon/SKILL.md').read_text()
        block=skill.split('```bash')[2].split('```')[0]
        with tempfile.TemporaryDirectory(prefix='scope example ') as folder:
            config=Path(folder)/'config.yaml'
            config.write_text('schema_version: 1\ntutorial_creator_vocab: unused-fixture.yaml\n')
            env=dict(os.environ,LEXICON_HOME=folder,S=str(SCRIPT),PYTHONDONTWRITEBYTECODE='1')
            shell=shutil.which('bash') or shutil.which('sh')
            if not shell: self.skipTest('No POSIX shell for documented shell example')
            r=subprocess.run([shell],input=block,env=env,capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            stored=yaml.safe_load((Path(folder)/'lists/swift.yaml').read_text())
            self.assertIn("identifiers from the user's own projects",stored['scope']['exclude'])

if __name__=='__main__': unittest.main()
