"""Release identifiers and data packaging must move together."""
import json
from pathlib import Path

from app.main import app

ROOT = Path(__file__).resolve().parents[2]


def test_0544_backend_and_miniapp_versions_match():
    package = json.loads((ROOT / 'miniapp/package.json').read_text(encoding='utf-8'))
    assert app.version == package['version'] == '0.5.44'


def test_0544_backend_packaging_requires_catalogue_and_migration():
    script = (ROOT / 'scripts/package_cloudbase_backend.ps1').read_text(encoding='utf-8')
    requirements = script.split('$required = @(', 1)[1].split('\n)', 1)[0]
    for relative in (
        'alembic/versions/0030_expand_strength_exercises.py',
        'app/data/exercise_catalog_v1.json',
        'app/services/exercise_catalog_v1.py',
        'app/services/exercise_search.py',
    ):
        assert f'"./{relative}"' in requirements
        assert (ROOT / 'backend' / relative).is_file()
