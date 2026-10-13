import hashlib
import json
from dataclasses import asdict

import pytest

from optomind_optics.harness.science.dataset_contracts import DatasetCard
from optomind_optics.harness.science.reality_readiness import ReadinessItem, assess_dataset_readiness, main


def card(tmp_path):
    raw = tmp_path/'raw.csv'; raw.write_text('500,0.6\n')
    return DatasetCard('sample','https://example.org/dataset','CC-BY-4.0',
        (('raw.csv',hashlib.sha256(raw.read_bytes()).hexdigest()),),(('sample','batch'),),
        (('wavelength_units','nm'),),None,None,'raw_measured',('exploratory_plotting',),('angle','polarization'))


def test_missing_conditions_never_become_inference_or_physical_authority(tmp_path):
    result = assess_dataset_readiness(card(tmp_path), tmp_path)
    assert result.hardware_authorized is False
    assert {'angle_deg','polarization','uncertainty','quantitative_inference','laboratory_authorization'}.issubset(result.pending())
    assert next(item for item in result.items if item.resource_id == 'quantitative_inference').status == 'blocked'


def test_explicit_resource_evidence_required():
    with pytest.raises(ValueError, match='explicit evidence'):
        ReadinessItem(resource_id='lab', category='permission', status='verified')


def test_real_cli_is_readonly_and_output_immutable(tmp_path, capsys):
    source = tmp_path/'card.json'; source.write_text(json.dumps(asdict(card(tmp_path))))
    output = tmp_path/'readiness.json'
    args = ['--dataset-card',str(source),'--source-root',str(tmp_path),'--output',str(output)]
    main(args)
    assert json.loads(capsys.readouterr().out)['hardware_authorized'] is False
    before = output.read_bytes()
    with pytest.raises(FileExistsError): main(args)
    assert output.read_bytes() == before
    (tmp_path/'raw.csv').write_text('500,0.9\n')
    with pytest.raises(ValueError, match='modified'): assess_dataset_readiness(DatasetCard(**json.loads(source.read_text())),tmp_path)


def test_manual_protocol_preview_consumes_readiness_without_authority(tmp_path, capsys):
    from test_science_c21_manual_import import fixture
    from optomind_optics.harness.tools.external_measurement import main as manual_main
    repo, protocol, frozen, returned, dataset, split, record = fixture(tmp_path)
    try:
        checklist = assess_dataset_readiness(dataset, tmp_path)
        ready = tmp_path/'ready.json'; ready.write_text(checklist.model_dump_json())
        path = tmp_path/'protocol_input.json'; path.write_text(protocol.model_dump_json())
        before = repo.latest_snapshot().snapshot_id
        manual_main(['preview','--protocol',str(path),'--readiness',str(ready)])
        report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert report['readiness_hash'] == checklist.content_hash
        assert 'laboratory_authorization' in report['pending_resources']
        assert report['hardware_authorized'] is False
        assert repo.latest_snapshot().snapshot_id == before
    finally:
        repo.close()
