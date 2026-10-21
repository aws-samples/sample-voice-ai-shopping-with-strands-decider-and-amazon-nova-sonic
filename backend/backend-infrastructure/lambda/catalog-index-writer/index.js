/**
 * catalog-index-writer: rebuilds the catalog search index (ADR-32).
 *
 * The search collection is reachable only from the VPC, so the catalog loader
 * (VoiceShop-CatalogCuration, outside the VPC) embeds the products, writes the
 * finished documents to the images bucket as NDJSON and invokes this function
 * with the object key. It recreates the products index, bulk-loads the documents
 * and waits until they are searchable. Classic vector collections reject
 * caller-chosen document ids, so a reload rebuilds the index instead of upserting.
 *
 * Event: { key: "catalog-index/<file>.ndjson" }
 * Returns: { indexed, searchable }
 */
const { S3Client, GetObjectCommand } = require('@aws-sdk/client-s3');
const { aossRequest } = require('../shared/opensearch');

const s3 = new S3Client({});
const INDEX = process.env.INDEX_NAME;
const BUCKET = process.env.IMAGES_BUCKET_NAME;
const KEY_PREFIX = 'catalog-index/';
const BULK_BATCH = 100;
const REFRESH_WAIT_MS = 180_000;
const EMBEDDING_DIMENSIONS = 1024;

const INDEX_BODY = {
  settings: { 'index.knn': true },
  mappings: {
    properties: {
      asin: { type: 'keyword' },
      title: { type: 'text' },
      brand: { type: 'keyword' },
      department: { type: 'keyword' },
      category: { type: 'keyword' },
      gender: { type: 'keyword' },
      color: { type: 'keyword' },
      priceCents: { type: 'integer' },
      available: { type: 'boolean' },
      rating: { type: 'float' },
      ratingCount: { type: 'integer' },
      embedding: {
        type: 'knn_vector',
        dimension: EMBEDDING_DIMENSIONS,
        method: { name: 'hnsw', engine: 'faiss', space_type: 'innerproduct', parameters: { m: 16, ef_construction: 128 } },
      },
    },
  },
};

exports.handler = async (event) => {
  const key = event && event.key;
  if (typeof key !== 'string' || !key.startsWith(KEY_PREFIX) || key.includes('..')) {
    throw new Error(`key must be under ${KEY_PREFIX}`);
  }
  const object = await s3.send(new GetObjectCommand({ Bucket: BUCKET, Key: key }));
  const documents = (await object.Body.transformToString()).split('\n').filter((line) => line.trim());

  const { status } = await aossRequest('HEAD', `/${INDEX}`, undefined, { okStatuses: [200, 404] });
  if (status === 200) {
    await aossRequest('DELETE', `/${INDEX}`);
    console.log(`🗑️ Deleted index ${INDEX}`);
  }
  await aossRequest('PUT', `/${INDEX}`, INDEX_BODY);
  console.log(`✅ Created index ${INDEX}`);

  let indexed = 0;
  for (let start = 0; start < documents.length; start += BULK_BATCH) {
    const lines = documents.slice(start, start + BULK_BATCH)
      .flatMap((doc) => [JSON.stringify({ index: { _index: INDEX } }), doc]);
    const result = await aossRequest('POST', '/_bulk', `${lines.join('\n')}\n`, { ndjson: true });
    const errors = (result.items || []).filter((item) => item.index && item.index.error);
    if (errors.length) {
      throw new Error(`bulk had ${errors.length} errors, first: ${JSON.stringify(errors[0].index.error).slice(0, 300)}`);
    }
    indexed += (result.items || []).length;
  }

  // Serverless collections make new documents searchable after a short delay.
  let searchable = 0;
  const deadline = Date.now() + REFRESH_WAIT_MS;
  while (Date.now() < deadline) {
    searchable = (await aossRequest('POST', `/${INDEX}/_count`, { query: { match_all: {} } })).count || 0;
    if (searchable >= indexed) break;
    await new Promise((resolve) => setTimeout(resolve, 10_000));
  }
  console.log(`🔎 ${searchable}/${indexed} documents searchable in ${INDEX}`);
  return { indexed, searchable };
};
