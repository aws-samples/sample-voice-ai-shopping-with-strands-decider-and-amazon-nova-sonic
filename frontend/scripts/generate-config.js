#!/usr/bin/env node
/**
 * Writes .env.local for the frontend from the CDK outputs that
 * deploy-all.sh saves:
 *   backend/backend-infrastructure/cdk-outputs/backend-infrastructure.json  (Cognito)
 *   backend/agentcore-runtime/cdk/cdk-outputs/agentcore-runtime.json        (session host)
 * .env.local is gitignored.
 */

import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const BACKEND = join(ROOT, 'backend/backend-infrastructure/cdk-outputs');
const RUNTIME = join(ROOT, 'backend/agentcore-runtime/cdk/cdk-outputs/agentcore-runtime.json');
const ENV_FILE = join(ROOT, 'frontend/.env.local');

function readStack(file, stack) {
  if (!existsSync(file)) return null;
  return JSON.parse(readFileSync(file, 'utf8'))[stack] ?? null;
}

const cognito = readStack(join(BACKEND, 'backend-infrastructure.json'), 'VoiceShop-CognitoStack')
  ?? readStack(join(BACKEND, 'cognito.json'), 'VoiceShop-CognitoStack');
const runtime = readStack(RUNTIME, 'VoiceShop-AgentCoreRuntimeStack');

if (!cognito || !runtime) {
  console.error('❌ Missing CDK outputs for the live frontend:');
  if (!cognito) console.error('   - VoiceShop-CognitoStack (run deploy-all.sh)');
  if (!runtime) console.error('   - VoiceShop-AgentCoreRuntimeStack (session host)');
  process.exit(1);
}

const values = {
  VITE_REGION: process.env.VOICESHOP_REGION || 'us-east-1',
  VITE_USER_POOL_ID: cognito.UserPoolId,
  VITE_CLIENT_ID: cognito.UserPoolClientId,
  VITE_IDENTITY_POOL_ID: cognito.IdentityPoolId,
  VITE_RUNTIME_ARN: runtime.AgentRuntimeArn,
};
const missing = Object.entries(values).filter(([, v]) => !v).map(([k]) => k);
if (missing.length) {
  console.error(`❌ CDK outputs are missing: ${missing.join(', ')}`);
  process.exit(1);
}

writeFileSync(ENV_FILE, `# Generated from CDK outputs by scripts/generate-config.js. Do not commit.\n${
  Object.entries(values).map(([k, v]) => `${k}=${v}`).join('\n')}\n`);
console.log(`✅ Wrote ${ENV_FILE}`);
