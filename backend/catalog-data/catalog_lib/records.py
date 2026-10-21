"""DynamoDB item, OpenSearch document and embedding text builders for the
synthetic catalog. Keys are deterministic, so a rerun overwrites rather than
duplicates."""

import re
from decimal import Decimal

CATALOG_META_KEY = {'PK': 'CATALOG#META', 'SK': 'META'}
ASIN = re.compile(r'^[A-Z0-9]{10}$')
GENDERS = {'women', 'men', 'unisex', 'girls', 'boys'}
REQUIRED = ('asin', 'title', 'brand', 'department', 'category', 'gender', 'color', 'material', 'priceCents',
            'averageRating', 'ratingNumber', 'features', 'description', 'available', 'image')


def problems(product):
    """Why a catalog row cannot be loaded (empty list: it can)."""
    found = [f'missing {f}' for f in REQUIRED if product.get(f) in (None, '')]
    if not ASIN.match(str(product.get('asin', ''))):
        found.append('asin is not 10 characters A-Z0-9')
    if product.get('gender') not in GENDERS:
        found.append(f"unknown gender {product.get('gender')!r}")
    if not isinstance(product.get('priceCents'), int) or product.get('priceCents', 0) <= 0:
        found.append('priceCents is not a positive integer')
    return found


def product_key(asin):
    return {'PK': f'PRODUCT#{asin}', 'SK': 'META'}


def image_key(asin):
    """One 384 px WebP per product, for the shelf card and the detail panel."""
    return f'products/{asin}/image.webp'


def embedding_text(product):
    """title | brand | department | category | gender | color | material | first 3 features."""
    parts = [product['title'], product['brand'], product['department'], product['category'],
             product['gender'], product['color'], product['material']]
    parts.extend(product['features'][:3])
    return ' | '.join(p for p in parts if p)


def dynamodb_item(product):
    item = {
        **product_key(product['asin']),
        'asin': product['asin'],
        'title': product['title'],
        'brand': product['brand'],
        'department': product['department'],
        'category': product['category'],
        'gender': product['gender'],
        'color': product['color'],
        'material': product['material'],
        'size': product.get('size'),
        'priceCents': product['priceCents'],
        'averageRating': Decimal(str(product['averageRating'])),
        'ratingNumber': product['ratingNumber'],
        'features': product['features'],
        'description': product['description'],
        'imageKeys': {'large': image_key(product['asin'])},
        'available': product['available'],
    }
    return {k: v for k, v in item.items() if v is not None}   # no NULLs for optional fields (size)


def opensearch_document(product, embedding):
    return {
        'asin': product['asin'],
        'title': product['title'],
        'brand': product['brand'],
        'department': product['department'],
        'category': product['category'],
        'gender': product['gender'],
        'color': product['color'],
        'priceCents': product['priceCents'],
        'available': product['available'],
        'rating': product['averageRating'],
        'ratingCount': product['ratingNumber'],
        'embedding': embedding,
    }


def descriptions(taxonomy):
    """Option descriptions for the decider: a department lists its categories, a
    category lists example products (from the generator's taxonomy)."""
    departments, categories = {}, {}
    for dept in taxonomy or []:
        departments[dept['department']] = ', '.join(c['label'] for c in dept['categories'])
        for cat in dept['categories']:
            categories[cat['label']] = 'e.g. ' + ', '.join(cat['ideas'][:5])
    return departments, categories


def catalog_meta_item(products, counts, source, curated_at, taxonomy=None):
    prices = [p['priceCents'] for p in products]
    departments = {}
    for p in products:
        departments.setdefault(p['department'], set()).add(p['category'])
    department_descriptions, category_descriptions = descriptions(taxonomy)
    return {
        **CATALOG_META_KEY,
        'departments': {d: sorted(c) for d, c in sorted(departments.items())},
        'departmentDescriptions': {d: t for d, t in department_descriptions.items() if d in departments},
        'categoryDescriptions': {c: t for c, t in category_descriptions.items()
                                 if any(c in cats for cats in departments.values())},
        'categories': sorted({p['category'] for p in products}),
        'genders': sorted({p['gender'] for p in products}),
        'itemCount': len(products),
        'availableCount': sum(1 for p in products if p['available']),
        'minPriceCents': min(prices) if prices else 0,
        'maxPriceCents': max(prices) if prices else 0,
        'counts': counts,
        'source': source,
        'curatedAt': curated_at,
    }
