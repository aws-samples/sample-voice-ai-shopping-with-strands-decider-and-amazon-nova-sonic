from decimal import Decimal

from catalog_lib.records import (
    CATALOG_META_KEY, catalog_meta_item, dynamodb_item, embedding_text, image_key,
    opensearch_document, problems, product_key,
)

PRODUCT = {
    'asin': 'V7709897A5', 'title': 'PixelRiot Puzzle-Cube Print Tee', 'brand': 'PixelRiot', 'department': 'Kids & Teens',
    'category': 'Teen Streetwear', 'categoryId': 'teen-streetwear', 'gender': 'boys', 'color': 'yellow',
    'material': 'cotton', 'size': None, 'priceCents': 2399, 'averageRating': 4.4, 'ratingNumber': 182,
    'features': ['a', 'b', 'c', 'd'], 'description': 'Soft.', 'available': True, 'image': 'images/V7709897A5.webp',
}


def test_keys_are_deterministic():
    assert product_key('V7709897A5') == {'PK': 'PRODUCT#V7709897A5', 'SK': 'META'}
    assert image_key('V7709897A5') == 'products/V7709897A5/image.webp'


def test_dynamodb_item_matches_lambda_contract():
    item = dynamodb_item(PRODUCT)
    assert item['PK'] == 'PRODUCT#V7709897A5' and item['department'] == 'Kids & Teens'
    assert item['imageKeys'] == {'large': 'products/V7709897A5/image.webp'}
    assert not any(k.startswith('GSI') for k in item)
    assert item['averageRating'] == Decimal('4.4') and 'size' not in item and 'image' not in item


def test_opensearch_document_fields():
    doc = opensearch_document(PRODUCT, [0.1] * 1024)
    assert set(doc) == {'asin', 'title', 'brand', 'department', 'category', 'gender', 'color', 'priceCents',
                        'available', 'rating', 'ratingCount', 'embedding'}


def test_embedding_text_has_department_category_and_first_three_features():
    assert embedding_text(PRODUCT) == ('PixelRiot Puzzle-Cube Print Tee | PixelRiot | Kids & Teens | Teen Streetwear | '
                                       'boys | yellow | cotton | a | b | c')


def test_problems_catch_bad_rows():
    assert problems(PRODUCT) == []
    assert 'unknown gender' in ' '.join(problems({**PRODUCT, 'gender': 'kids'}))
    assert problems({**PRODUCT, 'asin': 'short'}) and problems({**PRODUCT, 'priceCents': 0})


def test_catalog_meta_item_groups_categories_by_department():
    other = {**PRODUCT, 'asin': 'V000000001', 'department': 'Toys & Games', 'category': 'Puzzles & Cubes',
             'priceCents': 1499, 'available': False}
    item = catalog_meta_item([PRODUCT, other], {'rows': 2}, {'kind': 'synthetic'}, '2026-10-19T00:00:00+00:00')
    assert {k: item[k] for k in CATALOG_META_KEY} == CATALOG_META_KEY
    assert item['departments'] == {'Kids & Teens': ['Teen Streetwear'], 'Toys & Games': ['Puzzles & Cubes']}
    assert item['itemCount'] == 2 and item['availableCount'] == 1 and (item['minPriceCents'], item['maxPriceCents']) == (1499, 2399)


def test_curation_refuses_a_zip_that_does_not_match_its_lock(tmp_path, monkeypatch):
    import json, zipfile
    import pytest
    import curate
    (tmp_path / 'c.zip').write_bytes(b'not the pinned file')
    (tmp_path / 'catalog.lock.json').write_text(json.dumps({'file': 'c.zip', 'sha256': '0' * 64}))
    monkeypatch.setattr(curate, 'LOCK', tmp_path / 'catalog.lock.json')
    with pytest.raises(SystemExit):
        curate.open_catalog()


def test_option_descriptions_come_from_the_taxonomy():
    from catalog_lib.records import descriptions
    taxonomy = [{'department': 'Toys & Games', 'categories': [
        {'label': 'Puzzles & Cubes', 'ideas': ['3x3 speed cube', 'jigsaw puzzle']},
        {'label': 'Board Games', 'ideas': ['strategy game']}]}]
    departments, categories = descriptions(taxonomy)
    assert departments == {'Toys & Games': 'Puzzles & Cubes, Board Games'}
    assert categories['Puzzles & Cubes'] == 'e.g. 3x3 speed cube, jigsaw puzzle'
