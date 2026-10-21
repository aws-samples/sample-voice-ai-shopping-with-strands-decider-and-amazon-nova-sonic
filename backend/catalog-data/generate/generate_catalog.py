#!/usr/bin/env python3
"""Generates the VoiceShop synthetic catalog: product data and product photos.

Every product, brand, description and image is generated, so the catalog can
ship with the repository. Stages (each one resumable; finished work is skipped):

  specs    Claude on Amazon Bedrock writes products per category (taxonomy.json):
           invented brands, a distinctive title, attributes, price, rating and
           a photo prompt.
  images   Stability AI Stable Image Core on Amazon Bedrock draws one studio
           product photo per product (seeded per product, so a rerun draws the
           same image), downscaled to a small WebP.
  qa       Claude Haiku looks at each image and checks it shows the product in
           its color, with no text, logo or person; rejected images are redrawn
           with a new seed (up to --max-redraws times).
  whiten   For products that passed QA: Stability AI Remove Background cuts the
           product out of the full-size original, which is placed on pure white
           (#FFFFFF) and downscaled to the shipped WebP.
  package  Writes catalog.jsonl, the images and manifest.json (counts, sha256)
           into one zip.

'all' runs every stage, then tops up any category with fewer than
--per-category products that passed QA (more products written, drawn and
checked, up to TOP_UP_ROUNDS times).

Usage:
  python3 generate_catalog.py all --out ../catalog-build [--categories teen-streetwear,puzzles-cubes] [--per-category 50]
  python3 generate_catalog.py specs|images|qa|package --out ../catalog-build ...

AWS: the image model runs in --image-region (Stable Image Core is offered in
us-west-2); the text models use cross-region inference profiles. This runs once
by the maintainer; deployments only load the finished zip.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import io
import json
import logging
import re
import sys
import threading
import time
import zipfile
from pathlib import Path

import boto3
from botocore.config import Config
from PIL import Image

HERE = Path(__file__).resolve().parent
SPEC_MODEL = 'us.anthropic.claude-sonnet-5'
QA_MODEL = 'us.anthropic.claude-haiku-4-5-20251001-v1:0'
IMAGE_MODEL = 'stability.stable-image-core-v1:1'
CUTOUT_MODEL = 'us.stability.stable-image-remove-background-v1:0'   # on-demand only through this profile
TOP_UP_ROUNDS = 3
IMAGE_PX = 384
WEBP_QUALITY = 78
BATCH = 15   # products per writing call; larger batches got cut off mid-JSON
GENDERS = {'women', 'men', 'unisex', 'girls', 'boys'}
COLORS = ['black', 'white', 'grey', 'silver', 'gold', 'rose gold', 'brown', 'tan', 'beige', 'cream', 'navy', 'blue',
          'light blue', 'teal', 'green', 'olive', 'red', 'burgundy', 'pink', 'purple', 'lavender', 'orange', 'yellow',
          'multicolor']
PHOTO_STYLE = ('professional e-commerce studio product photograph, the single product centered, full product visible, '
               'plain unbranded product with no lettering, isolated on a pure white seamless background (#FFFFFF), '
               'bright even studio lighting, very soft natural shadow under the product, sharp focus, photorealistic')
# Clothing is shown the way stores show it without a model.
APPAREL_STYLE = 'shown as a neat flat lay or on an invisible ghost mannequin, nobody wearing it, no hanger'
# Devices are shown with the screen switched off: a lit screen shows digits, which QA rejects as text.
DEVICE_STYLE = 'the screen is switched off, plain dark glass with no display content'
EXTRA = 1.1   # write ~10% more products than needed, so enough pass image QA
NEGATIVE = ('grey background, colored background, gradient background, hanger, clothes hanger, hook, stand, '
            'text, words, letters, numbers, logo, brand name, watermark, label text, people, person, model, hands, face, '
            'mannequin, multiple products, collage, cluttered background, blurry, low quality, cartoon, illustration')

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger('generate')
_lock = threading.Lock()


def taxonomy(selected: list[str] | None) -> list[dict]:
    data = json.loads((HERE / 'taxonomy.json').read_text())
    out = []
    for dept in data['departments']:
        for cat in dept['categories']:
            if not selected or cat['id'] in selected:
                out.append({**cat, 'department': dept['id'], 'departmentLabel': dept['label']})
    if selected and len(out) != len(selected):
        missing = set(selected) - {c['id'] for c in out}
        sys.exit(f'Unknown categories: {", ".join(sorted(missing))}')
    return out


def product_id(category: str, index: int) -> str:
    """10 characters, A-Z0-9, stable for a category and position (the session host's id format)."""
    return 'V' + hashlib.sha1(f'{category}:{index}'.encode(), usedforsecurity=False).hexdigest()[:9].upper()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def append_jsonl(path: Path, rows: list[dict]) -> None:
    with _lock, path.open('a') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')


def converse(client, model: str, prompt: str, image: bytes | None = None, max_tokens: int = 16000) -> str:
    content = [{'text': prompt}]
    if image is not None:
        content.insert(0, {'image': {'format': 'webp', 'source': {'bytes': image}}})
    for attempt in range(5):
        try:
            # Newer Claude models take no temperature; QA (an older Haiku) runs deterministic at 0.
            config = {'maxTokens': max_tokens, **({'temperature': 0} if image is not None else {})}
            response = client.converse(modelId=model, messages=[{'role': 'user', 'content': content}], inferenceConfig=config)
            # Newer models may return a reasoning block first: take the text block.
            return next(block['text'] for block in response['output']['message']['content'] if 'text' in block)
        except client.exceptions.ThrottlingException:
            time.sleep(2 ** attempt * 2)
    raise RuntimeError(f'{model} kept throttling')


def json_block(text: str):
    match = re.search(r'```(?:json)?\s*(.*?)```', text, re.S)
    return json.loads(match.group(1) if match else text)


# --------------------------------------------------------------------- specs
SPEC_PROMPT = """You are writing product listings for VoiceShop, a fictional online store used to demonstrate a voice shopping assistant.

Department: {department}
Category: {label}
Write exactly {count} different products for this category.

Rules:
- Brands are invented: {brand_rule}. Never use a real company, brand, character, sports team or person.
- Each title is distinctive and at most 60 characters, so a shopper can tell products apart by name ("Sunburst Linen Wrap Dress", not "Women's Dress"). Titles never mention logos, text or printing rules.
- Vary the products: types (ideas: {ideas}), colors, styles, materials ({materials}) and price points.
- gender is one of: {genders}. Spread products across these.
- color is one main color from: {colors}.
- price_usd is within {price_min}-{price_max}.
- rating is 3.4-4.9 with one decimal; rating_count is 5-4000.
- features: 3 to 5 short phrases. description: 2 or 3 plain sentences.
- Any print or graphic on a product is a pattern, shape or picture, never words, letters, numbers or a logo.
- image_prompt describes only what a camera would see of the physical product: shape, color, material and visible details.
  Describe clothing as an object on its own (fabric, cut, seams, pockets, hem, color), never as worn: no body parts
  (thigh, hip, waist fit, bust, leg shape on a person) and no words about see-through fabric (sheer, translucent). Describe it so it can be drawn: say "an assembled jigsaw puzzle showing a coral reef, with visible interlocking piece lines", not "1000 pieces". No text, logos, people or background.
- Already written in this category (do not repeat): {existing}

Return only a JSON array in a ```json block. Each item:
{{"title": str, "brand": str, "gender": str, "color": str, "material": str, "size": str or null, "price_usd": number,
  "rating": number, "rating_count": int, "features": [str], "description": str, "image_prompt": str}}"""


def valid_spec(spec: dict, cat: dict) -> bool:
    try:
        return (spec['gender'] in cat['genders'] and spec['color'] in COLORS and 0 < len(spec['title']) <= 70
                and cat['price'][0] <= float(spec['price_usd']) <= cat['price'][1] and 3 <= len(spec['features']) <= 5
                and 3.0 <= float(spec['rating']) <= 5.0 and spec['image_prompt'] and spec['description'])
    except (KeyError, TypeError, ValueError):
        return False


def stage_specs(args, cats: list[dict], targets: dict[str, int] | None = None) -> None:
    client = boto3.client('bedrock-runtime', region_name=args.text_region, config=Config(read_timeout=300))
    path = args.out / 'specs.jsonl'
    have = read_jsonl(path)

    def one(cat: dict) -> None:
        mine = [s for s in have if s['category'] == cat['id']]
        brands = sorted({s['brand'] for s in mine})
        target = (targets or {}).get(cat['id'], round(args.per_category * EXTRA))
        while len(mine) < target:
            count = min(BATCH, target - len(mine))
            prompt = SPEC_PROMPT.format(
                department=cat['departmentLabel'], label=cat['label'], count=count, ideas=', '.join(cat['ideas']),
                materials=', '.join(cat['materials']), genders=', '.join(cat['genders']), colors=', '.join(COLORS),
                price_min=cat['price'][0], price_max=cat['price'][1],
                brand_rule=(f'reuse some of {", ".join(brands)} and add new ones' if brands else
                            'create 6 to 8 brand names for this category and reuse them across products'),
                existing='; '.join(s['title'] for s in mine) or 'none')
            try:
                batch = json_block(converse(client, SPEC_MODEL, prompt))
            except (ValueError, RuntimeError) as exc:
                log.warning('⚠️ %s: unreadable batch (%s), retrying', cat['id'], exc)
                continue
            titles = {s['title'].lower() for s in mine}
            rows = []
            for spec in batch:
                if len(mine) + len(rows) >= target:
                    break
                if not valid_spec(spec, cat) or spec['title'].lower() in titles:
                    continue
                titles.add(spec['title'].lower())
                index = len(mine) + len(rows)
                rows.append({'id': product_id(cat['id'], index), 'category': cat['id'], 'categoryLabel': cat['label'],
                             'department': cat['department'], 'departmentLabel': cat['departmentLabel'],
                             'photo': cat.get('photo'), **spec})
            append_jsonl(path, rows)
            mine += rows
            brands = sorted({s['brand'] for s in mine})
            log.info('📝 %s: %d/%d products', cat['id'], len(mine), target)

    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(one, cats))


# -------------------------------------------------------------------- images
def draw(client, spec: dict, attempt: int) -> bytes:
    seed = int(hashlib.sha1(f"{spec['id']}:{attempt}".encode(), usedforsecurity=False).hexdigest()[:8], 16)
    extra = {'apparel': APPAREL_STYLE, 'device': DEVICE_STYLE}.get(spec.get('photo'))
    style = f"{extra}, {PHOTO_STYLE}" if extra else PHOTO_STYLE
    body = {'prompt': f"{spec['image_prompt']}. {style}", 'negative_prompt': NEGATIVE,
            'aspect_ratio': '1:1', 'seed': seed, 'output_format': 'jpeg'}
    for wait in range(6):
        try:
            response = client.invoke_model(modelId=IMAGE_MODEL, body=json.dumps(body),
                                           contentType='application/json', accept='application/json')
            break
        except client.exceptions.ThrottlingException:
            time.sleep(2 ** wait * 2)
    else:
        raise RuntimeError('image model kept throttling')
    payload = json.loads(response['body'].read())
    if payload.get('finish_reasons', [None])[0] not in (None, 'SUCCESS') or not payload.get('images'):
        raise RuntimeError(f"no image: {payload.get('finish_reasons')}")
    return base64.b64decode(payload['images'][0])   # the full-size JPEG original


def small_webp(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert('RGB').resize((IMAGE_PX, IMAGE_PX), Image.LANCZOS).save(buffer, 'WEBP', quality=WEBP_QUALITY, method=6)
    return buffer.getvalue()


def stage_images(args, cats: list[dict], redraw: dict[str, int] | None = None) -> None:
    client = boto3.client('bedrock-runtime', region_name=args.image_region, config=Config(read_timeout=120))
    images = args.out / 'images'
    for folder in ('images', 'originals', 'final'):
        (args.out / folder).mkdir(parents=True, exist_ok=True)
    wanted = {c['id'] for c in cats}
    specs = [s for s in read_jsonl(args.out / 'specs.jsonl') if s['category'] in wanted]
    todo = [s for s in specs if (redraw and s['id'] in redraw) or not (images / f"{s['id']}.webp").exists()]
    done = [0]

    def one(spec: dict) -> None:
        attempt = (redraw or {}).get(spec['id'], 0)
        try:
            original = draw(client, spec, attempt)
            (args.out / 'originals' / f"{spec['id']}.jpg").write_bytes(original)
            (images / f"{spec['id']}.webp").write_bytes(small_webp(Image.open(io.BytesIO(original))))
            (args.out / 'final' / f"{spec['id']}.webp").unlink(missing_ok=True)   # a redraw needs a new cutout
        except RuntimeError as exc:
            log.warning('⚠️ %s: %s', spec['id'], exc)
            return
        with _lock:
            done[0] += 1
            if done[0] % 25 == 0 or done[0] == len(todo):
                log.info('🖼️  %d/%d images', done[0], len(todo))

    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(one, todo))


# ------------------------------------------------------------------------ qa
QA_PROMPT = """This image should be a studio product photo of: "{title}" ({color}, {material}). Category: {category}.
Answer in a ```json block: {{"matches": bool, "has_text_or_logo": bool, "has_person": bool, "note": str}}
matches is true only if the main object is clearly this kind of product and mostly {color}.
has_text_or_logo is true if words, letters, a brand name or a logo appear on the product or in the image. Numbers on
a watch dial, or a clock and icons on a device's screen, are part of the product and do not count."""


def stage_qa(args, cats: list[dict]) -> None:
    client = boto3.client('bedrock-runtime', region_name=args.text_region, config=Config(read_timeout=120))
    wanted = {c['id'] for c in cats}
    specs = {s['id']: s for s in read_jsonl(args.out / 'specs.jsonl') if s['category'] in wanted}
    for round_ in range(args.max_redraws + 1):
        results = {r['id']: r for r in read_jsonl(args.out / 'qa.jsonl')}
        todo = [s for i, s in specs.items() if (args.out / 'images' / f'{i}.webp').exists()
                and (i not in results or (not results[i]['ok'] and results[i]['attempt'] < round_))]
        if not todo:
            break

        def one(spec: dict) -> dict:
            image = (args.out / 'images' / f"{spec['id']}.webp").read_bytes()
            try:
                verdict = json_block(converse(client, QA_MODEL, QA_PROMPT.format(
                    title=spec['title'], color=spec['color'], material=spec['material'],
                    category=spec['categoryLabel']), image=image, max_tokens=300))
            except (ValueError, RuntimeError) as exc:
                verdict = {'matches': False, 'note': f'qa failed: {exc}'}
            ok = bool(verdict.get('matches')) and not verdict.get('has_text_or_logo') and not verdict.get('has_person')
            return {'id': spec['id'], 'ok': ok, 'attempt': round_, **verdict}

        with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
            verdicts = list(pool.map(one, todo))
        append_jsonl(args.out / 'qa.jsonl', verdicts)
        rejected = {v['id']: round_ + 1 for v in verdicts if not v['ok']}
        log.info('🔎 QA round %d: %d checked, %d rejected', round_, len(verdicts), len(rejected))
        if rejected and round_ < args.max_redraws:
            stage_images(args, cats, redraw=rejected)


# -------------------------------------------------------------------- whiten
def passed(args) -> dict[str, dict]:
    verdicts = {}
    for row in read_jsonl(args.out / 'qa.jsonl'):
        verdicts[row['id']] = row   # the last verdict wins
    return {i: v for i, v in verdicts.items() if v['ok']}


def stage_whiten(args, cats: list[dict]) -> None:
    client = boto3.client('bedrock-runtime', region_name=args.image_region, config=Config(read_timeout=120))
    wanted = {c['id'] for c in cats}
    ok = passed(args)
    todo = [s['id'] for s in read_jsonl(args.out / 'specs.jsonl')
            if s['category'] in wanted and s['id'] in ok and not (args.out / 'final' / f"{s['id']}.webp").exists()]
    done = [0]

    def one(pid: str) -> None:
        original = (args.out / 'originals' / f'{pid}.jpg').read_bytes()
        body = {'image': base64.b64encode(original).decode(), 'output_format': 'png'}
        for wait in range(6):
            try:
                response = client.invoke_model(modelId=CUTOUT_MODEL, body=json.dumps(body),
                                               contentType='application/json', accept='application/json')
                break
            except client.exceptions.ThrottlingException:
                time.sleep(2 ** wait * 2)
        else:
            log.warning('⚠️ %s: background removal kept throttling', pid)
            return
        cutout = Image.open(io.BytesIO(base64.b64decode(json.loads(response['body'].read())['images'][0]))).convert('RGBA')
        white = Image.new('RGBA', cutout.size, (255, 255, 255, 255))
        white.alpha_composite(cutout)
        (args.out / 'final' / f'{pid}.webp').write_bytes(small_webp(white))
        with _lock:
            done[0] += 1
            if done[0] % 50 == 0 or done[0] == len(todo):
                log.info('⬜ %d/%d on white', done[0], len(todo))

    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(one, todo))


# ------------------------------------------------------------------- package
def price_cents(usd) -> int:
    """Store-style prices: a whole-dollar price becomes .99 below it ($25 -> $24.99); others stay as written."""
    cents = int(round(float(usd) * 100))
    return cents - 1 if cents % 100 == 0 else cents


def stage_package(args, cats: list[dict]) -> None:
    wanted = {c['id'] for c in cats}
    ok = passed(args)
    products, kept = [], {}
    for spec in read_jsonl(args.out / 'specs.jsonl'):
        if spec['category'] not in wanted or spec['id'] not in ok or not (args.out / 'final' / f"{spec['id']}.webp").exists():
            continue
        if kept.get(spec['category'], 0) >= args.per_category:
            continue   # the extra ~10% only fill in for products that failed QA
        kept[spec['category']] = kept.get(spec['category'], 0) + 1
        n = int(spec['id'][1:], 16)
        products.append({
            'asin': spec['id'], 'title': spec['title'], 'brand': spec['brand'], 'category': spec['categoryLabel'],
            'categoryId': spec['category'], 'department': spec['departmentLabel'], 'gender': spec['gender'],
            'color': spec['color'], 'material': spec['material'], 'size': spec.get('size'),
            'priceCents': price_cents(spec['price_usd']), 'averageRating': float(spec['rating']),
            'ratingNumber': int(spec['rating_count']), 'features': spec['features'], 'description': spec['description'],
            'available': n % 20 != 0, 'image': f"images/{spec['id']}.webp"})
    zip_path = args.out / f'{args.name}.zip'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as z:   # WebP is already compressed
        z.writestr('catalog.jsonl', ''.join(json.dumps(p, ensure_ascii=False) + '\n' for p in products))
        for p in products:
            z.write(args.out / 'final' / f"{p['asin']}.webp", p['image'])
        z.writestr('manifest.json', json.dumps({
            'name': args.name, 'products': len(products), 'categories': len({p['categoryId'] for p in products}),
            'imagePx': IMAGE_PX, 'models': {'specs': SPEC_MODEL, 'qa': QA_MODEL, 'images': IMAGE_MODEL, 'cutout': CUTOUT_MODEL},
            # The session host's department and category questions describe each option with these.
            'taxonomy': [{'department': d, 'categories': [{'label': c['label'], 'ideas': c['ideas']} for c in cats if c['departmentLabel'] == d]}
                         for d in dict.fromkeys(c['departmentLabel'] for c in cats)],
            'note': 'Synthetic catalog: every product, brand, description and image is generated.'}, indent=1))
    shortfall = {c: args.per_category - n for c, n in kept.items() if n < args.per_category}
    shortfall.update({c['id']: args.per_category for c in cats if c['id'] not in kept})
    if shortfall:
        log.warning('⚠️ categories short of %d products: %s', args.per_category, shortfall)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    log.info('📦 %s: %d products, %.1f MB, sha256 %s', zip_path.name, len(products), zip_path.stat().st_size / 1e6, digest)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('stage', choices=['specs', 'images', 'qa', 'whiten', 'package', 'all'])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--categories', help='comma-separated category ids (default: all 50)')
    parser.add_argument('--per-category', type=int, default=50)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--max-redraws', type=int, default=2)
    parser.add_argument('--text-region', default='us-west-2')
    parser.add_argument('--image-region', default='us-west-2')
    parser.add_argument('--name', default='voiceshop-catalog-v2')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cats = taxonomy(args.categories.split(',') if args.categories else None)
    if args.stage != 'all':
        log.info('=== %s (%d categories) ===', args.stage, len(cats))
        globals()[f'stage_{args.stage}'](args, cats)
        return
    for stage in ('specs', 'images', 'qa'):
        log.info('=== %s (%d categories) ===', stage, len(cats))
        globals()[f'stage_{stage}'](args, cats)
    for round_ in range(TOP_UP_ROUNDS):
        ok = passed(args)
        specs = read_jsonl(args.out / 'specs.jsonl')
        short = {}
        for cat in cats:
            mine = [s for s in specs if s['category'] == cat['id']]
            missing = args.per_category - sum(1 for s in mine if s['id'] in ok)
            if missing > 0:
                short[cat['id']] = len(mine) + int(missing * 1.5) + 1
        if not short:
            break
        log.info('=== top-up %d: %s ===', round_ + 1, {c: t for c, t in short.items()})
        few = [c for c in cats if c['id'] in short]
        stage_specs(args, few, targets=short)
        stage_images(args, few)
        stage_qa(args, few)
    for stage in ('whiten', 'package'):
        log.info('=== %s (%d categories) ===', stage, len(cats))
        globals()[f'stage_{stage}'](args, cats)


if __name__ == '__main__':
    main()
