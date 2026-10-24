/**
 * Cart reads shared by add-to-cart and place-order. Reads are strongly
 * consistent (a write is visible to the read right after it), and totals are
 * always summed from integer priceCents, never floats. Lines past their
 * expiresAt are skipped: DynamoDB deletes expired items only eventually, and a
 * cart must not outlive the session state that owns it.
 */
const { QueryCommand } = require('@aws-sdk/lib-dynamodb');
const { ddb } = require('./aws');

function cartPk(sessionId) {
  return `CART#${sessionId}`;
}

async function readCartItems(sessionId) {
  const items = [];
  let exclusiveStartKey;
  do {
    const page = await ddb.send(
      new QueryCommand({
        TableName: process.env.CART_TABLE_NAME,
        KeyConditionExpression: 'PK = :pk AND begins_with(SK, :item)',
        FilterExpression: 'attribute_not_exists(expiresAt) OR expiresAt > :now',
        ExpressionAttributeValues: { ':pk': cartPk(sessionId), ':item': 'ITEM#', ':now': Math.floor(Date.now() / 1000) },
        ExclusiveStartKey: exclusiveStartKey,
        ConsistentRead: true,
      }),
    );
    items.push(...(page.Items || []));
    exclusiveStartKey = page.LastEvaluatedKey;
  } while (exclusiveStartKey);
  return items;
}

/** { items: [{asin, title, quantity, priceCents, lineTotalCents}], itemCount, totalCents } */
function summarizeCart(cartItems) {
  const items = cartItems
    .map((item) => ({
      asin: item.SK.slice('ITEM#'.length),
      title: item.title,
      quantity: item.quantity,
      priceCents: item.priceCents,
      lineTotalCents: item.priceCents * item.quantity,
    }))
    .sort((a, b) => a.asin.localeCompare(b.asin));
  return {
    items,
    itemCount: items.reduce((sum, item) => sum + item.quantity, 0),
    totalCents: items.reduce((sum, item) => sum + item.lineTotalCents, 0),
  };
}

/** The reasons of a cancelled DynamoDB transaction, one per item ('None' when that item was fine). */
function cancellationReasons(err) {
  if (err.name !== 'TransactionCanceledException') return null;
  return (err.CancellationReasons || []).map((reason) => reason.Code || 'None');
}

module.exports = { cartPk, readCartItems, summarizeCart, cancellationReasons };
