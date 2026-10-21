/**
 * place_order tool (Gateway Lambda target, called only from session host code
 * after the shopper confirms). No payment is processed.
 *
 * Reads the cart, then writes the order and empties the cart in one DynamoDB
 * transaction. The orderId comes from sessionId + idempotencyKey, so a retry
 * returns the order it already placed. Each cart line is deleted on the
 * condition that its quantity is still the one read: an add that lands in
 * between cancels the transaction, which is retried on a fresh read, so it is
 * never lost.
 *
 * Input:  { sessionId, idempotencyKey }
 * Output: { order: {orderId, items, itemCount, totalCents, placedAt} }
 *      or { error: "EmptyCart" | "CartTooLarge" | "CartChanged" }
 */
const crypto = require('crypto');
const { GetCommand, TransactWriteCommand } = require('@aws-sdk/lib-dynamodb');
const { ddb } = require('../shared/aws');
const { cartPk, readCartItems, summarizeCart, cancellationReasons } = require('../shared/cart');
const { SESSION_ID_PATTERN, runTool, requireString } = require('../shared/validation');

const MAX_LINES = 99;   // a transaction holds at most 100 items: the order plus the lines
const ATTEMPTS = 3;

function parse(input) {
  return {
    sessionId: requireString(input, 'sessionId', { min: 36, max: 100, pattern: SESSION_ID_PATTERN }),
    idempotencyKey: requireString(input, 'idempotencyKey', { min: 1, max: 140 }),
  };
}

function orderIdFor(sessionId, idempotencyKey) {
  return `ord-${crypto.createHash('sha256').update(`${sessionId}#${idempotencyKey}`).digest('hex').slice(0, 24)}`;
}

async function getOrder(orderId) {
  const { Item } = await ddb.send(
    new GetCommand({
      TableName: process.env.ORDERS_TABLE_NAME, Key: { PK: `ORDER#${orderId}`, SK: 'META' }, ConsistentRead: true,
    }),
  );
  return Item ? toOrder(Item) : null;
}

function toOrder(item) {
  return { orderId: item.orderId, items: item.items, itemCount: item.itemCount, totalCents: item.totalCents, placedAt: item.placedAt };
}

async function run({ sessionId, idempotencyKey }) {
  const orderId = orderIdFor(sessionId, idempotencyKey);
  for (let attempt = 1; attempt <= ATTEMPTS; attempt += 1) {
    const existing = await getOrder(orderId);
    if (existing) {
      console.log(`ℹ️ place_order: request already placed ${orderId}`);
      return { order: existing };
    }
    const cartItems = await readCartItems(sessionId);
    if (cartItems.length === 0) return { error: 'EmptyCart', message: 'The cart has no items' };
    if (cartItems.length > MAX_LINES) return { error: 'CartTooLarge', message: `At most ${MAX_LINES} different products per order` };
    const cart = summarizeCart(cartItems);
    const orderItem = {
      PK: `ORDER#${orderId}`, SK: 'META', orderId, sessionId, idempotencyKey,
      items: cart.items, itemCount: cart.itemCount, totalCents: cart.totalCents, placedAt: new Date().toISOString(),
    };
    try {
      await ddb.send(
        new TransactWriteCommand({
          TransactItems: [
            { Put: { TableName: process.env.ORDERS_TABLE_NAME, Item: orderItem, ConditionExpression: 'attribute_not_exists(PK)' } },
            ...cartItems.map((item) => ({
              Delete: {
                TableName: process.env.CART_TABLE_NAME,
                Key: { PK: cartPk(sessionId), SK: item.SK },
                ConditionExpression: 'quantity = :q',
                ExpressionAttributeValues: { ':q': item.quantity },
              },
            })),
          ],
        }),
      );
      console.log(`✅ place_order: ${orderId}, ${cart.itemCount} items, ${cart.totalCents} cents`);
      return { order: toOrder(orderItem) };
    } catch (err) {
      const reasons = cancellationReasons(err);
      if (!reasons) throw err;
      // reasons[0]: a concurrent retry placed this order (returned next loop).
      // Any other: the cart changed while being read; read it again.
      console.log(`ℹ️ place_order: transaction cancelled (${reasons.join(',')}), attempt ${attempt}`);
    }
  }
  return { error: 'CartChanged', message: 'The cart kept changing; please try again' };
}

exports.handler = (event) => runTool('place_order', event, parse, run);
