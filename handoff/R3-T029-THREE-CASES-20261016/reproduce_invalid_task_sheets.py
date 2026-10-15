"""Zero-fee fixture replay of the six recorded production validation failures."""
from pathlib import Path
import hashlib
import importlib.util
import json

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parents[1]
SOURCE = REPO/'focused_research'/'task_sheet.py'
expected = json.loads((PACKAGE/'VALIDATOR_PROVENANCE.json').read_text(encoding='utf-8'))['sha256']
assert hashlib.sha256(SOURCE.read_bytes()).hexdigest()==expected, 'Use the fixed result checkout for reproduction'
spec=importlib.util.spec_from_file_location('t029_fixture_validator', SOURCE)
validator=importlib.util.module_from_spec(spec);spec.loader.exec_module(validator)
count=0
for case in sorted((PACKAGE/'cases').iterdir()):
    if not case.is_dir():continue
    question=json.loads((case/'FRESH_REQUEST.json').read_text(encoding='utf-8'))['question']
    outcome=json.loads((case/'plan'/'planning.json').read_text(encoding='utf-8'))
    for record in outcome['rounds']:
        sheet={**record['raw_task_sheet'], 'original_question':question}
        try:
            validator.validate(sheet,question=question)
        except (ValueError,TypeError) as exc:
            assert str(exc)==record['error'], (case.name,record['round'],str(exc))
            print(case.name, 'round',record['round'], str(exc))
            count+=1
        else:
            raise AssertionError('Recorded invalid fixture unexpectedly accepted')
assert count==6
print('Reproduced 6 recorded failures; no API/retrieval/solver calls.')
