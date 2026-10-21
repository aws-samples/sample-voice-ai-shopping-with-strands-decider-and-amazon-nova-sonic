/**
 * remove_from_cart tool (Gateway Lambda target, called only from session host code).
 *
 * Deletes one cart line. Deleting a line that is already gone changes nothing, so
 * a retried request is safe without an idempotency record.
 *
 * Input:  { sessionId, asin }
 * Output: { cart: {items, itemCount, totalCents} }
 */
const { DeleteCommand } = require('@aws-sdk/lib-dynamodb');
const { ddb } = require('../shared/aws');
const { cartPk, readCartItems, summarizeCart } = require('../shared/cart');
const { ASIN_PATTERN, SESSION_ID_PATTERN, runTool, requireString } = require('../shared/validation');

function parse(input) {
  return {
    sessionId: requireString(input, 'sessionId', { min: 36, max: 100, pattern: SESSION_ID_PATTERN }),
    asin: requireString(input, 'asin', { min: 10, max: 10, pattern: ASIN_PATTERN }),
  };
}

async function run({ sessionId, asin }) {
  await ddb.send(new DeleteCommand({
    TableName: process.env.CART_TABLE_NAME,
    Key: { PK: cartPk(sessionId), SK: `ITEM#${asin}` },
  }));
  console.log(`✅ remove_from_cart: ${asin}`);
  return { cart: summarizeCart(await readCartItems(sessionId)) };
}

exports.handler = (event) => runTool('remove_from_cart', event, parse, run);
