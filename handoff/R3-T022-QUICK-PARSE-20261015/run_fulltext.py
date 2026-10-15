"""Use the unchanged pinned bridge on five explicitly selected source records."""
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
SOURCE = Path(r'D:\OptoMind-Article-3\r3t022a\focused_research')
REVIEW = Path(r'D:\OptoMind-Article-3\r3t022r3')
os.environ['OPENALEX_API_KEYS_FILE'] = r'F:\OptoMind-Review-2\api_keys\openalex.txt'
os.environ['CORE_API_KEYS_FILE'] = r'F:\OptoMind-Review-2\api_keys\core_api.txt'
contact = next((x.strip() for x in Path(r'F:\OptoMind-Review-2\api_keys\Unpaywall.txt').read_text(
    encoding='utf-8-sig').splitlines() if x.strip() and '@' in x), '')
if contact:
    os.environ['UNPAYWALL_EMAIL'] = contact
    os.environ['OPENALEX_EMAIL'] = contact
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_DATASETS_OFFLINE'] = '1'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ.pop('PYTHONPATH', None)

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

assert not (ROOT/'FULLTEXT_STARTED.json').exists(), 'Do not duplicate started acquisition/parsing'
discovery = read(ROOT/'runs/fulltext-discovery/DISCOVERY_RESULT.json')
batches = read(ROOT/'runs/s2/raw_retrieval_records.json')
side = dict(batches[1]['rows'][4]['paper'])
assert side['corpusId'] == '119291481'
observed = re.search(r'https://arxiv\.org/abs/([0-9.]+)', side['openAccessInfo']['disclaimer'])
assert observed
side.update(arxiv_id=observed.group(1), source_url=observed.group(0),
    canonical_paper_id='corpus:119291481', query_ids=['Q2'], goal_ids=['G1','G2'],
    discovery_reference='runs/s2/raw_retrieval_records.json:call_index=1,row=4,paper.openAccessInfo.disclaimer')
gaas = dict(batches[2]['rows'][0]['raw_metadata'])
assert gaas['externalIds']['DOI'] == '10.4302/plp.v17i4.1361'
gaas.update(query_ids=['Q3'], goal_ids=['G1','G2'])
records = [side, gaas, discovery['records'][5], discovery['records'][4], discovery['records'][2]]
assert len(records) == 5
save(ROOT/'SELECTED_FULLTEXT_RECORDS.json', {'records':records})
save(ROOT/'FULLTEXT_SELECTION.json', {
    'independent_discovery':'runs/fulltext-discovery/DISCOVERY_RESULT.json',
    'selection_by':'Operator relevance assessment of returned metadata; no model call or query expansion',
    'records':[
        {'title':side['title'],'reason':'Closest visible-reflect/NIR-transmit fused-silica application; crossover differs'},
        {'title':gaas['title'],'reason':'TiO2/SiO2 multilayer/TMM/angular-method transfer; reflects NIR rather than target function'},
        {'title':records[2]['title'],'reason':'Independent CORE full text, fused-silica coating and angular tradeoffs; opposite IR function'},
        {'title':records[3]['title'],'reason':'Independent OpenAlex 45-degree beam-splitter synthesis/deposition; different materials and bands'},
        {'title':records[4]['title'],'reason':'Independent OpenAlex complete multilayer fabrication/design reference; thermal IR bands differ'}],
    'cap':5, 'queries_repeated_for_acquisition':False,
    'note':'Provider discovery already completed once on the unchanged generated queries. The bridge receives selected records with queries=[] to avoid unreviewed first-hit papers displacing relevant sources.'})
save(ROOT/'FULLTEXT_STARTED.json', {'selected':5,'formula_enhancement':False,'simulation':False})
spec = importlib.util.spec_from_file_location('_t022_fulltext_stage',SOURCE/'fulltext_stage.py')
stage = importlib.util.module_from_spec(spec);sys.modules[spec.name]=stage;spec.loader.exec_module(stage)
result = stage.run_fulltext_stage(records, [], ROOT/'runs/fulltext', review_root=REVIEW,
    review_python=r'D:\OptoMind-Article-3\local-preparation\envs\review\Scripts\python.exe',
    docling_python=r'F:\OptoMind-Review-2\outputs\e2e_optics_20261012_80cny\parser_repair\venv_docling\Scripts\python.exe',
    docling_config=ROOT/'private/BASIC_PARALLEL3_CONFIG.json', max_papers=5, deadline_seconds=180,
    providers=('openalex','unpaywall','core','crossref','arxiv'),
    local_formula_config=None, formula_selected_ids=(), resume=True)
save(ROOT/'FULLTEXT_STAGE_RETURN.json',result)
print(json.dumps({'status':result.get('status'),'verified_exports':len(result.get('exports',[])),
    'docling_status':result.get('docling_status'),'records':[{'title':x.get('record',{}).get('title'),
    'status':x.get('status'),'error':x.get('error')} for x in result.get('records',[])]},ensure_ascii=False))
