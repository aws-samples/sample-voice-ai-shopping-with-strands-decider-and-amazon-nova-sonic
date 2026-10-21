# Catalog updates in production

VoiceShop ships with a generated catalog that is loaded as a whole on each deploy ([ADR-27](architecture-decisions.md#adr-27-synthetic-catalog)): the zip in the repository is checked by hash, and the search index is rebuilt on each load. A real store adds products all the time. This page describes how catalog updates would work in production, and how each update is checked with Strands Decider before shoppers see it. **It is not implemented in this sample** (ADR-25).

## Flow

```
 Product image uploaded            Amazon S3 (incoming/)
            │  object created event
            ▼
 Amazon EventBridge ──► AWS Step Functions (one run per batch of uploads)
            │
            ├─ 1. Extract   multimodal Amazon Nova model fills a fixed schema
            ├─ 2. Validate  schema, allowed values, duplicates
            ├─ 3. Stage     DynamoDB (status = staged), embedding, staging search index
            ├─ 4. Check     decider checks against the staged catalog (below)
            └─ 5. Publish   status = live, live index, CATALOG#META updated
                  or Flag   status = needs_review, with the failing check
```

### 1. Extract

A multimodal Amazon Nova model reads the image, plus any supplier text, and returns JSON in a fixed schema:

- **Facts:** `title`, `department` and `category` (one of the store's departments and one of its categories), `gender` (`women`, `men`, `unisex`, `girls`, `boys`), `color`, `material`, `style`, `features`.
- **Uncertainty:** a short list of attributes the model could not read from the image, such as material.

The schema is the same one `CATALOG#META` and the decider questions use, so a new item can never introduce a value the questions do not know about.

**Distinctive titles.** The prompt asks for a title that tells the item apart from similar ones: "Gold chain-link watch with three bracelets", not "Ladies Quartz Movement Analog Watch". On a real shelf, near-identical titles were the main cause of pointing errors (see "How we tuned it").

### 2. Validate

- **Code, not the model:** JSON schema, allowed values, price present, image readable, duplicate detection by image embedding.
- **Failures** go straight to review.

### 3. Stage

- **DynamoDB:** items are written with `status = staged`.
- **Search:** embeddings go into a staging copy of the search index.
- **Shoppers:** nothing staged is visible to them yet.

### 4. Check with Strands Decider

The check answers one question: can shoppers find, and refer to, the new items by voice? It runs the same question builders as the session host (`agent/questions.py`) against the staged catalog.

| Check | What runs | Pass |
|---|---|---|
| Understand questions | If the batch adds a department, category or gender value, the option lists change. Add scenarios for the new values to `eval/catalog/scenarios.jsonl` and re-run `eval/catalog/run_catalog_eval.py run`, then `score` | Every question still meets its calibrated precision |
| Findability | For each new item, a model writes 3–5 shopper sentences from its metadata ("a purple suede bag for my mom"), plus near-misses that should not match. Run retrieval and `best_fit` | The item is in the top 12 and `best_fit` does not pick `none_of_these`; near-misses do |
| Pointing | Build shelves that mix new items with their closest lookalikes (by embedding). Add pointing turns on those shelves to the `eval/catalog` scenarios and score them with `run_catalog_eval.py` | Open precision stays at the bar measured on the held-out set |

Results:

- **One item fails:** it is set to `needs_review` with the failing sentences and scores. The usual fix is better metadata, such as a more distinctive title, and that happens in step 1, not in the decider.
- **A whole question drops below its bar:** for example, many new lookalike watches hurt pointing. The batch is held, and a person reviews the thresholds in `agent/calibration.json`.

### 5. Publish

- **Status:** items move to `status = live`.
- **Index:** the staging documents are copied to the live index.
- **`CATALOG#META`:** counts, departments, categories, genders and the option descriptions are updated, so the session host's question options follow the catalog (ADR-30).

## Rules that keep the check honest

- **Thresholds are never retuned automatically.** A pipeline that lowers thresholds until its checks pass is tuning the result, not measuring it. Recalibration is a reviewed change, with its numbers committed next to the thresholds, as in "How we tuned it".
- **Generated sentences measure drift, not quality.** Sentences written from an item's own metadata partly test that metadata against itself. A human-written held-out set stays the reference, and is refreshed when the catalog changes a lot.
- **The check is about the questions as well as the catalog.** Decider confidence reflects how the questions and options are worded and what is in the catalog. A failing check can mean the metadata is unclear or that a question needs redesign. Both go to review.

## Byproducts

- **No-match calibration set:** the near-miss sentences from the findability check build up a labeled set of out-of-catalog requests. That is the data needed to re-derive the `no_match` threshold (ADR-21).
- **Catalog quality report:** each run reports which attributes the image model was unsure about, by category.

## Cost and scale notes

- **Batching:** uploads are grouped, for example every 15 minutes or every 100 images, so the decider check runs once per batch, not once per image.
- **Endpoint:** the checks use the same decider endpoint as shoppers. Run them off-peak, or give them a separate endpoint, so they don't compete with live traffic (ADR-22).
