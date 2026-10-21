/**
 * remove_from_cart tool (Gateway Lambda target, called only from session host code).
 *
 * Deletes one cart line, or with keepQuantity lowers it to that many ("remove one
 * of them"). Both set an end state rather than subtract, so a retried request
 * changes nothing further and is safe without an idempotency record.
 *
 * Input:  { sessionId, asin, keepQuantity? }   keepQuantity 1-9: the number left; absent or 0: delete the line
 * Output: { cart: {items, itemCount, totalCents} }
 */
const { DeleteCommand, UpdateCommand } = require('@aws-sdk/lib-dynamodb');
const { ddb } = require('../shared/aws');
const { cartPk, readCartItems, summarizeCart } = require('../shared/cart');
const { ASIN_PATTERN, SESSION_ID_PATTERN, runTool, requireString, optionalInt } = require('../shared/validation');

function parse(input) {
  return {
    sessionId: requireString(input, 'sessionId', { min: 36, max: 100, pattern: SESSION_ID_PATTERN }),
    asin: requireString(input, 'asin', { min: 10, max: 10, pattern: ASIN_PATTERN }),
    keepQuantity: optionalInt(input, 'keepQuantity', { min: 0, max: 9, defaultValue: 0 }),
  };
}

async function run({ sessionId, asin, keepQuantity }) {
  const key = { PK: cartPk(sessionId), SK: `ITEM#${asin}` };
  if (keepQuantity === 0) {
    await ddb.send(new DeleteCommand({ TableName: process.env.CART_TABLE_NAME, Key: key }));
    console.log(`✅ remove_from_cart: ${asin}`);
  } else {
    try {
      // Only lowers an existing line: a line that is gone, or already at or below
      // keepQuantity (a retry), is left as it is.
      await ddb.send(new UpdateCommand({
        TableName: process.env.CART_TABLE_NAME,
        Key: key,
        UpdateExpression: 'SET quantity = :k',
        ConditionExpression: 'attribute_exists(quantity) AND quantity > :k',
        ExpressionAttributeValues: { ':k': keepQuantity },
      }));
      console.log(`✅ remove_from_cart: ${asin} down to ${keepQuantity}`);
    } catch (err) {
      if (err.name !== 'ConditionalCheckFailedException') throw err;
      console.log(`ℹ️ remove_from_cart: ${asin} already at or below ${keepQuantity}, cart unchanged`);
    }
  }
  return { cart: summarizeCart(await readCartItems(sessionId)) };
}

exports.handler = (event) => runTool('remove_from_cart', event, parse, run);
