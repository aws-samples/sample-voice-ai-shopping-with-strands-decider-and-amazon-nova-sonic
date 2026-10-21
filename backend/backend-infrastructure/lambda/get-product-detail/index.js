/**
 * get_product_detail tool (Gateway Lambda target, read-only; the only Gateway
 * tool Sonic itself may call, ADR-15).
 *
 * Input:  { asin }
 * Output: { product: {...full record, images: {largeUrl}} } or { error: "NotFound" }
 */
const { GetCommand } = require('@aws-sdk/lib-dynamodb');
const { ddb, presignImage, productKey, imageKey } = require('../shared/aws');
const { ASIN_PATTERN, runTool, requireString } = require('../shared/validation');

// GSI1PK/GSI1SK: written by catalog loads before the index was removed (2026-10-20).
const INTERNAL_FIELDS = new Set(['PK', 'SK', 'GSI1PK', 'GSI1SK', 'imageKeys']);

function parse(input) {
  return { asin: requireString(input, 'asin', { min: 10, max: 10, pattern: ASIN_PATTERN }) };
}

async function run({ asin }) {
  const { Item } = await ddb.send(new GetCommand({ TableName: process.env.PRODUCTS_TABLE_NAME, Key: productKey(asin) }));
  if (!Item) return { error: 'NotFound', message: `No product with asin ${asin}` };

  const product = { asin };
  for (const [field, value] of Object.entries(Item)) {
    if (!INTERNAL_FIELDS.has(field)) product[field] = value;
  }
  product.images = { largeUrl: await presignImage(Item.imageKeys?.large || imageKey(product.asin)) };
  console.log(`✅ get_product_detail: ${asin}`);
  return { product };
}

exports.handler = (event) => runTool('get_product_detail', event, parse, run);
exports._test = { parse };
