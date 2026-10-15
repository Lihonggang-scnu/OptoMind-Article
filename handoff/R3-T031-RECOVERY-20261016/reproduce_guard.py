"""Zero-fee reproduction on the preserved actual S2 inputs; no text changes."""
import json
from pathlib import Path
import sys
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'focused_research'))
from material_evidence import prompt_evidence_view
for path in sorted((Path(__file__).parent/'cases').glob('*/evidence_items.json')):
    evidence=json.loads(path.read_text(encoding='utf-8'))
    try:
        prompt_evidence_view(evidence)
        print(path.parent.name, 'projection accepted')
    except ValueError as exc:
        print(path.parent.name, type(exc).__name__, str(exc), 'items=',len(evidence['items']),
              'text_chars=',sum(len(item['text']) for item in evidence['items']))
