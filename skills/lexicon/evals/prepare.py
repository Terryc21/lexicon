#!/usr/bin/env python3
"""Materialize a behavioral case in a new disposable directory; never install skills."""
import argparse
import json
from pathlib import Path
import shutil
import yaml


def prepare(case, dest):
    dest = Path(dest).resolve()
    if dest.exists():
        raise ValueError('Destination must not exist; choose a fresh disposable directory.')
    src = Path(__file__).resolve().parent
    case_data = next(e for e in json.loads((src/'evals.json').read_text())['evals'] if e['id']==case)
    dest.mkdir(parents=True)
    shutil.copytree(src/'fixtures', dest/'fixtures')
    homes = {1:'home', 2:'fixtures/e2v2-home', 3:'fixtures/e3-home', 4:'fixtures/e3-home', 5:'fixtures/e3-home'}
    home = dest/homes[case]
    home.mkdir(exist_ok=True)
    vocab = dest/('fixtures/tcproj/tutorials/vocabulary.yaml' if case==4 else 'fixtures/tc/vocabulary.yaml')
    # Set an explicit fixture path so status/init/dupes cannot consult a real registry.
    cfg = {'schema_version':1,'default_list':'swift' if case!=1 else None,'tutorial_creator_vocab':str(vocab)}
    (home/'config.yaml').write_text(yaml.safe_dump(cfg,sort_keys=False))
    result={'case':case,'cwd':str(dest),'lexicon_home':str(home),'vocabulary':str(vocab),
            'skill':str(src.parent),'prompt':case_data['prompt'],'user_answers':case_data['user_answers'],
            'expected_output':case_data['expected_output'],'live_result':'not run'}
    (dest/'case.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case',type=int,choices=range(1,6),required=True)
    p.add_argument('--dest',required=True)
    a=p.parse_args()
    try:
        print(json.dumps(prepare(a.case,a.dest),indent=2))
    except (OSError,ValueError) as error:
        p.exit(1,str(error)+'\n')
