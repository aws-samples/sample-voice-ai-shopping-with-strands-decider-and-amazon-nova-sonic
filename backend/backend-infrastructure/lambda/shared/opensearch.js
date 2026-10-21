/**
 * Minimal SigV4-signed client for the OpenSearch Serverless collection (service
 * name "aoss"). Reaches the collection through the AOSS VPC endpoint: the
 * collection's network policy allows no public access.
 */
const { SignatureV4 } = require('@smithy/signature-v4');
const { HttpRequest } = require('@smithy/protocol-http');
const { NodeSha256 } = require('./aws');
const { defaultProvider } = require('@aws-sdk/credential-provider-node');

const signer = new SignatureV4({
  service: 'aoss',
  region: process.env.AWS_REGION,
  credentials: defaultProvider(),
  sha256: NodeSha256,
});

/**
 * One signed request. body: an object (sent as JSON) or, with ndjson, a string.
 * okStatuses: the statuses that count as success (default: any 2xx); the
 * response is { status, body } when okStatuses is given, else the parsed body.
 */
async function aossRequest(method, path, body, { ndjson = false, okStatuses } = {}) {
  const endpoint = new URL(process.env.COLLECTION_ENDPOINT);
  const payload = body === undefined ? undefined : (ndjson ? body : JSON.stringify(body));
  const request = new HttpRequest({
    method,
    protocol: 'https:',
    hostname: endpoint.hostname,
    path,
    headers: {
      host: endpoint.hostname,
      'content-type': ndjson ? 'application/x-ndjson' : 'application/json',
      // AOSS requires the payload hash header on every signed request.
      'x-amz-content-sha256': await sha256Hex(payload || ''),
    },
    body: payload,
  });
  const signed = await signer.sign(request);
  const response = await fetch(`https://${endpoint.hostname}${path}`, {
    method,
    headers: signed.headers,
    body: payload,
  });
  const text = await response.text();
  const ok = okStatuses ? okStatuses.includes(response.status) : response.ok;
  if (!ok) {
    throw new Error(`OpenSearch ${method} ${path} failed: HTTP ${response.status} ${text.slice(0, 500)}`);
  }
  const parsed = text ? JSON.parse(text) : {};
  return okStatuses ? { status: response.status, body: parsed } : parsed;
}

async function sha256Hex(text) {
  const hash = new NodeSha256();
  hash.update(text);
  return Buffer.from(await hash.digest()).toString('hex');
}

module.exports = { aossRequest };
