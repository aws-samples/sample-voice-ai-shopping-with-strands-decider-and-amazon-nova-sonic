/**
 * search_catalog tool (Gateway Lambda target). Embeds the shopper's query with
 * Titan Text Embeddings V2 and runs a filtered k-NN search on the catalog
 * collection. Price, category, gender and availability are applied as an
 * OpenSearch filter here AND re-applied in the session host's filters.py
 *: this Lambda narrows, it is not the only guard.
 *
 * Input:  { query, maxPriceCents?, minPriceCents?, categories?, genders?, availableOnly?, limit? }
 * Output: { totalCatalog, totalMatched, hits: [{asin, title, brand, category, gender, color,
 *           priceCents, rating, ratingCount, imageUrl, score}],
 *           timingsMs: {embed, search, presign, total} }   (handler-side, for ui.debug)
 */
const { BedrockRuntimeClient, InvokeModelCommand } = require('@aws-sdk/client-bedrock-runtime');
const { presignImage, imageKey } = require('../shared/aws');
const { aossRequest } = require('../shared/opensearch');
const {
  GENDERS, MAX_PRICE_CENTS, ValidationError,
  runTool, requireString, optionalInt, optionalBool, optionalLabelList, optionalEnumList,
} = require('../shared/validation');

const EMBEDDING_MODEL_ID = 'amazon.titan-embed-text-v2:0';
const EMBEDDING_DIMENSIONS = 1024;
const TOTAL_CATALOG_CACHE_MS = 5 * 60 * 1000;
const SOURCE_FIELDS = ['asin', 'title', 'brand', 'department', 'category', 'gender', 'color', 'priceCents', 'rating', 'ratingCount', 'available'];

const bedrock = new BedrockRuntimeClient({});
let totalCatalogCache = { value: null, fetchedAt: 0 };

function parse(input) {
  const query = requireString(input, 'query', { min: 1, max: 500 });
  const maxPriceCents = optionalInt(input, 'maxPriceCents', { min: 0, max: MAX_PRICE_CENTS });
  const minPriceCents = optionalInt(input, 'minPriceCents', { min: 0, max: MAX_PRICE_CENTS });
  if (minPriceCents !== undefined && maxPriceCents !== undefined && minPriceCents > maxPriceCents) {
    throw new ValidationError('minPriceCents must not be greater than maxPriceCents');
  }
  return {
    query,
    maxPriceCents,
    minPriceCents,
    departments: optionalLabelList(input, 'departments', 10),
    categories: optionalLabelList(input, 'categories', 50),
    genders: optionalEnumList(input, 'genders', GENDERS, 5),
    availableOnly: optionalBool(input, 'availableOnly', true),
    limit: optionalInt(input, 'limit', { min: 1, max: 200, defaultValue: 200 }),
  };
}

function buildFilters(input) {
  const filters = [];
  if (input.minPriceCents !== undefined || input.maxPriceCents !== undefined) {
    const range = {};
    if (input.minPriceCents !== undefined) range.gte = input.minPriceCents;
    if (input.maxPriceCents !== undefined) range.lte = input.maxPriceCents;
    filters.push({ range: { priceCents: range } });
  }
  if (input.departments) filters.push({ terms: { department: input.departments } });
  if (input.categories) filters.push({ terms: { category: input.categories } });
  if (input.genders) filters.push({ terms: { gender: input.genders } });
  if (input.availableOnly) filters.push({ term: { available: true } });
  return filters;
}

async function embed(text) {
  const response = await bedrock.send(
    new InvokeModelCommand({
      modelId: EMBEDDING_MODEL_ID,
      contentType: 'application/json',
      accept: 'application/json',
      body: JSON.stringify({ inputText: text, dimensions: EMBEDDING_DIMENSIONS, normalize: true }),
    }),
  );
  return JSON.parse(Buffer.from(response.body).toString('utf8')).embedding;
}

async function totalCatalog(indexPath) {
  if (totalCatalogCache.value !== null && Date.now() - totalCatalogCache.fetchedAt < TOTAL_CATALOG_CACHE_MS) {
    return totalCatalogCache.value;
  }
  const { count } = await aossRequest('POST', `${indexPath}/_count`, { query: { match_all: {} } });
  totalCatalogCache = { value: count, fetchedAt: Date.now() };
  return count;
}

async function run(input) {
  const indexPath = `/${process.env.INDEX_NAME}`;
  const filters = buildFilters(input);
  const filterQuery = filters.length ? { bool: { filter: filters } } : { match_all: {} };

  const startedAt = performance.now();
  const vector = await embed(input.query);
  const embeddedAt = performance.now();
  const [searchResult, countResult, catalogSize] = await Promise.all([
    aossRequest('POST', `${indexPath}/_search`, {
      size: input.limit,
      _source: SOURCE_FIELDS,
      query: {
        knn: {
          embedding: {
            vector,
            k: input.limit,
            ...(filters.length ? { filter: { bool: { filter: filters } } } : {}),
          },
        },
      },
    }),
    aossRequest('POST', `${indexPath}/_count`, { query: filterQuery }),
    totalCatalog(indexPath),
  ]);

  const searchedAt = performance.now();
  const hits = await Promise.all(
    (searchResult.hits?.hits || []).map(async (hit) => {
      const doc = hit._source;
      return {
        asin: doc.asin,
        title: doc.title,
        brand: doc.brand,
        department: doc.department,
        category: doc.category,
        gender: doc.gender,
        color: doc.color,
        priceCents: doc.priceCents,
        rating: doc.rating,
        ratingCount: doc.ratingCount,
        available: doc.available,
        imageUrl: await presignImage(imageKey(doc.asin)),
        score: hit._score,
      };
    }),
  );

  const doneAt = performance.now();
  const timingsMs = {
    embed: Math.round(embeddedAt - startedAt),
    search: Math.round(searchedAt - embeddedAt), // kNN, filtered count and (cached) catalog count, in parallel
    presign: Math.round(doneAt - searchedAt),
    total: Math.round(doneAt - startedAt),
  };
  console.log(`✅ search_catalog: ${hits.length} hits, ${countResult.count} matched of ${catalogSize}, ${JSON.stringify(timingsMs)}`);
  return { totalCatalog: catalogSize, totalMatched: countResult.count, hits, timingsMs };
}

exports.handler = (event) => runTool('search_catalog', event, parse, run);
exports._test = { parse, buildFilters };
