/**
 * Module-level AWS clients and helpers shared by the tool Lambdas (created once
 * per execution environment, reused across invocations).
 */
const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient } = require('@aws-sdk/lib-dynamodb');
const { createHash, createHmac } = require('node:crypto');
const { SignatureV4 } = require('@smithy/signature-v4');
const { HttpRequest } = require('@smithy/protocol-http');
const { defaultProvider } = require('@aws-sdk/credential-provider-node');

const PRESIGNED_URL_TTL_SECONDS = 3600; // ADR-13: 1-hour presigned image URLs

const ddb = DynamoDBDocumentClient.from(new DynamoDBClient({}), {
  marshallOptions: { removeUndefinedValues: true },
});
// SHA-256 / HMAC-SHA-256 on Node's native crypto, in the Hash shape SignatureV4 expects.
class NodeSha256 {
  constructor(secret) {
    this.hash = secret ? createHmac('sha256', toBuffer(secret)) : createHash('sha256');
  }
  update(data) {
    this.hash.update(toBuffer(data));
  }
  digest() {
    return Promise.resolve(new Uint8Array(this.hash.digest()));
  }
}

function toBuffer(data) {
  if (typeof data === 'string') return Buffer.from(data, 'utf8');
  if (ArrayBuffer.isView(data)) return Buffer.from(data.buffer, data.byteOffset, data.byteLength);
  return Buffer.from(data);
}

// One signer per execution environment. getSignedUrl builds a full S3 command
// middleware stack per URL, which cost ~2.5 ms each and ~490 ms for a 200-hit
// search_catalog page; this signer caches its signing key per day.
const region = process.env.AWS_REGION;
const presigner = new SignatureV4({
  service: 's3',
  region,
  credentials: defaultProvider(),
  sha256: NodeSha256,
  uriEscapePath: false,
  applyChecksum: false,
});

/** 1-hour presigned GET for an object in the images bucket. Never logged. */
async function presignImage(key) {
  if (!key) return null;
  const hostname = `${process.env.IMAGES_BUCKET_NAME}.s3.${region}.amazonaws.com`;
  const path = `/${key.split('/').map(encodeURIComponent).join('/')}`;
  // S3 presigned URLs sign the payload as UNSIGNED-PAYLOAD, sent only as a
  // signing input (as @aws-sdk/s3-request-presigner does), never as a header.
  const signed = await presigner.presign(
    new HttpRequest({
      method: 'GET', protocol: 'https:', hostname, path,
      headers: { host: hostname, 'x-amz-content-sha256': 'UNSIGNED-PAYLOAD' },
    }),
    { expiresIn: PRESIGNED_URL_TTL_SECONDS, unsignableHeaders: new Set(['x-amz-content-sha256']) },
  );
  const query = Object.entries(signed.query)
    .map(([name, value]) => `${encodeURIComponent(name)}=${encodeURIComponent(value)}`)
    .join('&');
  return `https://${hostname}${path}?${query}`;
}

function productKey(asin) {
  return { PK: `PRODUCT#${asin}`, SK: 'META' };
}

/** One 384 px WebP per product (backend/catalog-data), used for the shelf card and the detail panel. */
function imageKey(asin) {
  return `products/${asin}/image.webp`;
}

module.exports = { ddb, presignImage, productKey, imageKey, NodeSha256 };
