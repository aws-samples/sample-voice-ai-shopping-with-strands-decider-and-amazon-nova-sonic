# Catalog

VoiceShop ships its own generated catalog ([ADR-27](../../docs/architecture-decisions.md#adr-27-synthetic-catalog)). Every product, brand, description and image was made with models on Amazon Bedrock, so the catalog can live in this repository. There is no third-party product data.

- 10 departments with 5 categories each (50 categories), about 50 products per category (about 2,500 products).
- One 384 px WebP photo per product, on a pure white background.
- About 1 product in 20 is marked unavailable, to exercise the availability filter.

This package has two parts: the **generator**, run once by a maintainer, and the **loader**, run by every deploy.

## Pieces

| File | Runs | What it does |
|---|---|---|
| `generate/taxonomy.json` | read by both | Departments and their categories. Each category lists example products, materials, genders and a price range |
| `generate/generate_catalog.py` | once, by a maintainer | Writes the products, draws and checks the photos, and packages the zip (below) |
| `catalog/voiceshop-catalog-v2.zip` | read by the loader | `catalog.jsonl`, `images/` and `manifest.json` (counts, models, taxonomy) |
| `catalog/catalog.lock.json` | read by the loader | The zip's file name and sha256 |
| `curate.py` | `VoiceShop-CatalogCuration` (CodeBuild) | Loads the zip into S3, OpenSearch Serverless and DynamoDB |
| `catalog_lib/records.py` | loader | Row checks and the DynamoDB, OpenSearch and `CATALOG#META` record builders (AWS-free, unit tested) |
| `cdk/` | `deploy-all.sh` step 2 | `VoiceShop-CatalogDataStack`: the curation CodeBuild project and its permissions |
| `scripts/measure-retrieval-latency.py` | by hand | p50/p95 of `search_catalog` against the deployed catalog |

## Loader

`VoiceShop-CatalogCuration` runs without a VPC and without a source bucket: this package is the build source, uploaded as a CDK asset. The search collection is reachable only from its VPC (ADR-32), so the job hands the index documents to `VoiceShop-CatalogIndexWriter`, which runs there. `curate.py`:

1. checks the zip named in `catalog/catalog.lock.json` against its sha256 and stops if it differs;
2. checks every row and skips (and counts) invalid ones;
3. uploads each image to the private images bucket as `products/<asin>/image.webp`;
4. embeds each product with Amazon Titan Text Embeddings V2 (1,024 dimensions);
5. writes the index documents to the images bucket (`catalog-index/documents.ndjson`) and invokes `VoiceShop-CatalogIndexWriter`, which deletes and recreates the `products` index of the `voiceshop-catalog` collection, indexes every product and waits until they are searchable; the file is then removed;
6. writes `VoiceShop-Products` and `CATALOG#META` (departments with their categories, option descriptions from the taxonomy, genders, counts, price range);
7. removes products and images that are no longer in the catalog, and prints the counts.

Reruns create no duplicates. The search index is briefly empty while it is rebuilt.

`deploy-all.sh` runs it as step 2 (`--skip-catalog` skips it). To run it by hand:

```bash
cd cdk && npm ci
npx cdk deploy VoiceShop-CatalogDataStack                              # the project and its permissions
../../../codebuild-scripts/build-image.sh VoiceShop-CatalogCuration 20  # start the job and wait for it
```

## Generator

Run this only to change the catalog. Deploying does not need it.

**Needs:** Python 3.12 with `boto3` and `Pillow`, and Amazon Bedrock model access to Claude Sonnet 5, Claude Haiku 4.5, Stability AI Stable Image Core and Stability AI Remove Background. The image models run in us-west-2 (`--image-region`); the text models use cross-Region inference profiles (`--text-region`, default us-west-2).

Stages (each one resumable; finished work is skipped):

| Stage | What it does |
|---|---|
| `specs` | Claude Sonnet 5 writes the products for each category: invented brands, a distinctive title of at most 60 characters, gender, color, material, size, price, rating, features, description and a photo prompt. It writes about 10% extra, so enough pass image QA |
| `images` | Stable Image Core draws one studio photo per product, seeded per product |
| `qa` | Claude Haiku 4.5 checks each photo: the product in its color, no text, logo or person. Rejected photos are redrawn with a new seed, up to `--max-redraws` times (default 2) |
| `whiten` | Remove Background cuts the product out of the full-size photo and places it on pure white |
| `package` | Downscales to 384 px WebP and writes the zip, then prints its sha256 |

`all` runs every stage, then tops up any category with fewer than `--per-category` products that passed QA (up to 3 rounds).

```bash
python3 -m venv .venv && .venv/bin/pip install boto3 Pillow
cd generate
../.venv/bin/python generate_catalog.py all --out ../catalog-build               # all 50 categories, 50 products each
../.venv/bin/python generate_catalog.py all --out ../catalog-build --categories watches,rings --per-category 20
```

To ship a new catalog, copy `catalog-build/<name>.zip` into `catalog/`, set its file name and sha256 in `catalog/catalog.lock.json`, and commit both. The next deploy loads it and removes what is no longer in it.

A new catalog changes the decider's option lists. Update the labeled scenarios in `backend/agentcore-runtime/eval/catalog`, re-run `run_catalog_eval.py run` and `score`, and review the thresholds in `agent/calibration.json` before relying on it ([How we tuned it](../../docs/how-we-tuned-it.md)).

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest tests   # record builders, row checks, lock check
cd cdk && npx jest                                                                   # one project, no VPC, no OpenSearch access, IAM scope, cdk-nag
```
