/**
 * add_to_cart tool (Gateway Lambda target, called only from session host code).
 *
 * Compare-and-set: the caller says how many of the product it believes the cart
 * holds (expectedQuantity), and the line becomes one more than that only if the
 * cart really holds that many. A retry after a lost response therefore changes
 * nothing (the line already moved on) and returns the current cart with
 * conflict: true; no idempotency key has to be remembered anywhere. A line whose
 * TTL has passed counts as absent. A line holds at most 10 of a product.
 *
 * Input:  { sessionId, asin, expectedQuantity }
 * Output: { cart: {items, itemCount, totalCents}, conflict?: true }
 *      or { error: "NotFound" | "Unavailable" } or { error: "QuantityLimit", cart }
 */
const { GetCommand, UpdateCommand } = require('@aws-sdk/lib-dynamodb');
const { ddb, productKey } = require('../shared/aws');
const { cartPk, readCartItems, summarizeCart } = require('../shared/cart');
const { ASIN_PATTERN, SESSION_ID_PATTERN, runTool, requireString, optionalInt } = require('../shared/validation');

const CART_TTL_SECONDS = 24 * 60 * 60;
const MAX_PER_LINE = 10;

function parse(input) {
  return {
    sessionId: requireString(input, 'sessionId', { min: 36, max: 100, pattern: SESSION_ID_PATTERN }),
    asin: requireString(input, 'asin', { min: 10, max: 10, pattern: ASIN_PATTERN }),
    expectedQuantity: optionalInt(input, 'expectedQuantity', { min: 0, max: MAX_PER_LINE, defaultValue: 0 }),
  };
}

async function run({ sessionId, asin, expectedQuantity }) {
  if (expectedQuantity >= MAX_PER_LINE) {
    return { error: 'QuantityLimit', message: `At most ${MAX_PER_LINE} of one product per cart`,
             cart: summarizeCart(await readCartItems(sessionId)) };
  }
  const { Item: product } = await ddb.send(
    new GetCommand({ TableName: process.env.PRODUCTS_TABLE_NAME, Key: productKey(asin) }),
  );
  if (!product) return { error: 'NotFound', message: `No product with asin ${asin}` };
  if (product.available === false) return { error: 'Unavailable', message: `${asin} is not available` };

  const now = Math.floor(Date.now() / 1000);
  // The line as the caller saw it: absent (or expired) for 0, exactly expectedQuantity otherwise.
  const condition = expectedQuantity === 0
    ? 'attribute_not_exists(quantity) OR expiresAt <= :now'
    : 'quantity = :expected AND expiresAt > :now';
  try {
    await ddb.send(new UpdateCommand({
      TableName: process.env.CART_TABLE_NAME,
      Key: { PK: cartPk(sessionId), SK: `ITEM#${asin}` },
      UpdateExpression: 'SET quantity = :next, priceCents = :p, title = :t, expiresAt = :e',
      ConditionExpression: condition,
      ExpressionAttributeValues: {
        ':next': expectedQuantity + 1, ':now': now, ':p': product.priceCents, ':t': product.title,
        ':e': now + CART_TTL_SECONDS, ...(expectedQuantity === 0 ? {} : { ':expected': expectedQuantity }),
      },
    }));
    console.log(`✅ add_to_cart: ${asin} now ${expectedQuantity + 1}`);
  } catch (err) {
    if (err.name !== 'ConditionalCheckFailedException') throw err;
    // The cart no longer holds what the caller saw (a lost response that did commit, or another
    // tab): nothing changes, and the caller gets the cart as it is.
    console.log(`ℹ️ add_to_cart: ${asin} is not at ${expectedQuantity}; cart unchanged`);
    return { cart: summarizeCart(await readCartItems(sessionId)), conflict: true };
  }
  return { cart: summarizeCart(await readCartItems(sessionId)) };
}

exports.handler = (event) => runTool('add_to_cart', event, parse, run);
