"""Small, program-owned material capability vocabulary, separate from permission.

These reviewed chemical identities are ordinary inorganic dielectric coating
materials. This is deliberately not an exhaustive classification of the user's
allowed category, a transparency claim, or a deposition-process guarantee.
Unknown identities need review before execution, not a narrower user request.
Record existence, provenance/hash and full-grid n/k coverage remain independent
local registry preflight requirements; a well-formed inventory does not prove them.
"""
from __future__ import annotations


CATEGORY = 'ordinary_depositable_inorganic_dielectrics_no_metals'
REVIEWED_DIELECTRICS = {
    name.casefold(): name for name in
    ('HfO2', 'SiO2', 'MgF2', 'TiO2', 'Al2O3', 'Ta2O5', 'Nb2O5', 'ZrO2')
}


def canonical_dielectric(value):
    """Recognize only reviewed chemical spellings; never infer from optical k."""
    if not isinstance(value, str) or value != value.strip():
        return None
    return REVIEWED_DIELECTRICS.get(value.casefold())


def validate_material_capabilities(constraints):
    """Return explicit record mappings, or None for legacy/missing inventory.

    Callers distinguish missing category inventory from legacy finite permission.
    The mappings use the existing optical_media record fields, not model-authored
    category labels or purported verification flags.
    """
    present = {'allowed_material_category', 'execution_materials'} & set(constraints)
    if not present:
        return None
    if present != {'allowed_material_category', 'execution_materials'}:
        raise ValueError('material capabilities require category and execution_materials fields together')
    if constraints['allowed_material_category'] != CATEGORY:
        raise ValueError('unsupported allowed_material_category')
    if constraints.get('allowed_materials') is not None:
        raise ValueError('category permission requires allowed_materials=null; inventory is not user permission')
    inventory = constraints['execution_materials']
    if inventory is None:
        return None
    if not isinstance(inventory, dict) or not inventory:
        raise ValueError('execution_materials requires a nonempty explicit record mapping or null')
    for alias, record in inventory.items():
        canonical = canonical_dielectric(alias)
        if canonical is None or alias != canonical:
            raise ValueError('execution_materials alias requires a reviewed canonical dielectric identity')
        if not isinstance(record, dict) or set(record) != {'material', 'provider', 'dataset_id'}:
            raise ValueError('execution_materials record requires exactly material, provider, dataset_id')
        if canonical_dielectric(record['material']) != canonical:
            raise ValueError('execution_materials record chemical identity must match its alias')
        if record['provider'] not in ('local_csv', 'rii_sqlite'):
            raise ValueError('execution_materials requires a supported explicit local registry provider')
        dataset = record['dataset_id']
        if not ((isinstance(dataset, str) and dataset.strip() and dataset == dataset.strip())
                or (type(dataset) is int and dataset > 0)):
            raise ValueError('execution_materials requires an explicit nonempty dataset_id')
        if record['provider'] == 'local_csv':
            # The existing registry accepts canonical local CSV stems. Avoid
            # an alias claiming SiO2 while selecting a different local file.
            stem = dataset[:-4] if isinstance(dataset, str) and dataset.endswith('.csv') else dataset
            if canonical_dielectric(stem) != canonical:
                raise ValueError('execution_materials local dataset must match its chemical identity')
    return inventory
