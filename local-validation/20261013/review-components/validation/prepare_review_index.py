"""Build the production local search index from existing authentic material."""
import json
from pathlib import Path

from optomind_research.runtime.upgrade3 import progressive_review_plan as p
from optomind_research.runtime.upgrade3.planning_material_search import SearchIndexConfig, build_index

root = Path(__file__).resolve().parent / 'review'
pool = Path('D:/OptoMind-Article-3/local-functional-trial/20261013/review/planning-pool/PLANNING_POOL.jsonl')
rows = p.load_planning_pool(pool)
p._refresh_source_handles(rows)
identity = {'source_identity_map': p.ProgressiveReviewPlanner._source_identity_map(rows)}
identity_path = root / 'LOCAL_SOURCE_IDENTITIES.json'
identity_path.write_text(json.dumps(identity, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
config = SearchIndexConfig(
    topic_id='visible-achromatic-metalens-pilot-20261013',
    index_path=root / 'planning_material_index.sqlite', pool_path=pool,
    identity_map_path=identity_path,
)
report = build_index(config, report_path=root / 'LOCAL_INDEX_BUILD.json')
print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
