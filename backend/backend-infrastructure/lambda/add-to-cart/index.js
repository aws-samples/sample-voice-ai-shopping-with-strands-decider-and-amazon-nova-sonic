/**
 * add_to_cart tool (Gateway Lambda target, called only from session host code).
 *
 * One DynamoDB transaction writes an idempotency record for the request and
 * adds the quantity to the cart line. A retried request finds its own record and
 * changes nothing, whatever product it names. A line holds at most 10 of a
 * product.
 *
 * Input:  { sessionId, asin, quantity?, idempotencyKey }
 * Output: { cart: {items, itemCount, totalCents} }
 *      or { error: "NotFound" | "Unavailable" | "QuantityLimit" }
 */
const { GetCommand, TransactWriteCommand, UpdateCommand } = require('@aws-sdk/lib-dynamodb');
const { ddb, productKey } = require('../shared/aws');
const { cartPk, readCartItems, summarizeCart, cancellationReasons } = require('../shared/cart');
const { ASIN_PATTERN, SESSION_ID_PATTERN, runTool, requireString, optionalInt } = require('../shared/validation');

const CART_TTL_SECONDS = 24 * 60 * 60;
const MAX_PER_LINE = 10;

function parse(input) {
  return {
    sessionId: requireString(input, 'sessionId', { min: 36, max: 100, pattern: SESSION_ID_PATTERN }),
    asin: requireString(input, 'asin', { min: 10, max: 10, pattern: ASIN_PATTERN }),
    quantity: optionalInt(input, 'quantity', { min: 1, max: MAX_PER_LINE, defaultValue: 1 }),
    idempotencyKey: requireString(input, 'idempotencyKey', { min: 1, max: 140 }),
  };
}

async function run({ sessionId, asin, quantity, idempotencyKey }) {
  const { Item: product } = await ddb.send(
    new GetCommand({ TableName: process.env.PRODUCTS_TABLE_NAME, Key: productKey(asin) }),
  );
  if (!product) return { error: 'NotFound', message: `No product with asin ${asin}` };
  if (product.available === false) return { error: 'Unavailable', message: `${asin} is not available` };

  const now = Math.floor(Date.now() / 1000);
  const expiresAt = now + CART_TTL_SECONDS;
  const table = process.env.CART_TABLE_NAME;
  // An expired line that DynamoDB has not deleted yet starts again from zero, so adding to it
  // does not bring back its old quantity (cart reads already skip it).
  try {
    await ddb.send(new UpdateCommand({
      TableName: table,
      Key: { PK: cartPk(sessionId), SK: `ITEM#${asin}` },
      UpdateExpression: 'SET quantity = :zero',
      ConditionExpression: 'expiresAt <= :now',
      ExpressionAttributeValues: { ':zero': 0, ':now': now },
    }));
  } catch (err) {
    if (err.name !== 'ConditionalCheckFailedException') throw err;
  }
  try {
    await ddb.send(
      new TransactWriteCommand({
        TransactItems: [
          {
            Put: {
              TableName: table,
              Item: { PK: cartPk(sessionId), SK: `REQUEST#${idempotencyKey}`, expiresAt },
              ConditionExpression: 'attribute_not_exists(SK)',
            },
          },
          {
            Update: {
              TableName: table,
              Key: { PK: cartPk(sessionId), SK: `ITEM#${asin}` },
              UpdateExpression: 'ADD quantity :q SET priceCents = :p, title = :t, expiresAt = :e',
              ConditionExpression: 'attribute_not_exists(quantity) OR quantity <= :room',
              ExpressionAttributeValues: {
                ':q': quantity, ':room': MAX_PER_LINE - quantity, ':p': product.priceCents, ':t': product.title, ':e': expiresAt,
              },
            },
          },
        ],
      }),
    );
    console.log(`✅ add_to_cart: ${asin} x${quantity}`);
  } catch (err) {
    const reasons = cancellationReasons(err);
    if (!reasons) throw err;
    if (reasons[0] === 'ConditionalCheckFailed') {
      console.log(`ℹ️ add_to_cart: request ${idempotencyKey} already applied, cart unchanged`);
    } else if (reasons[1] === 'ConditionalCheckFailed') {
      return { error: 'QuantityLimit', message: `At most ${MAX_PER_LINE} of one product per cart` };
    } else {
      throw err;
    }
  }
  return { cart: summarizeCart(await readCartItems(sessionId)) };
}

exports.handler = (event) => runTool('add_to_cart', event, parse, run);
