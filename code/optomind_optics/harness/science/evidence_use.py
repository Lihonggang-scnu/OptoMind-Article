from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceUseRecord:
    acquisition_hash: str
    candidate_id: str
    purpose: str
    sequence: int
    block_ids: tuple[str, ...] = ()


class EvidenceUseGraph:
    purposes = frozenset({'fit', 'trigger', 'propose', 'select', 'validate', 'test'})

    def __init__(self, records: tuple[EvidenceUseRecord, ...] = ()):
        self.records: list[EvidenceUseRecord] = []
        for record in sorted(records, key=lambda item: item.sequence):
            self.add(record)

    def add(self, record: EvidenceUseRecord) -> None:
        if record.purpose not in self.purposes or not record.candidate_id or len(record.acquisition_hash) != 64:
            raise ValueError('invalid evidence identity or role')
        if self.records and record.sequence <= self.records[-1].sequence:
            raise ValueError('evidence-use sequence must increase')
        if record.purpose in {'validate', 'test'}:
            contamination = [old for old in self.records
                             if old.acquisition_hash == record.acquisition_hash
                             and old.candidate_id == record.candidate_id
                             and old.purpose in {'fit', 'trigger', 'propose', 'select'}]
            if contamination:
                raise ValueError('evidence influenced this candidate and cannot independently validate it')
        self.records.append(record)

    def inherit_candidate(self, parent_id: str, child_id: str) -> None:
        inherited = [record for record in self.records if record.candidate_id == parent_id
                     and record.purpose in {'fit', 'trigger', 'propose', 'select'}]
        for record in inherited:
            self.add(EvidenceUseRecord(record.acquisition_hash, child_id, 'propose',
                                       self.records[-1].sequence + 1, record.block_ids))
