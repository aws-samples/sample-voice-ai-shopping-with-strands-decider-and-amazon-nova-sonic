#!/usr/bin/env node
/**
 * Builds the live frontend and deploys it to the VoiceShop-FrontendStack Amplify
 * app (manual deployment: create-deployment, upload the zip, start-deployment),
 * adapted from the template.
 */

const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const FRONTEND = path.join(__dirname, '..');
const OUTPUTS = path.join(FRONTEND, 'cdk', 'cdk-outputs', 'frontend.json');
const REGION = process.env.VOICESHOP_REGION || 'us-east-1';

function fail(message) {
  console.error(`❌ ${message}`);
  process.exit(1);
}

if (!fs.existsSync(OUTPUTS)) fail('frontend/cdk/cdk-outputs/frontend.json not found: deploy VoiceShop-FrontendStack first.');
const stack = JSON.parse(fs.readFileSync(OUTPUTS, 'utf8'))['VoiceShop-FrontendStack'] || {};
if (!stack.AmplifyAppId) fail('AmplifyAppId missing from the frontend stack outputs.');

console.log('🔨 Building the live frontend...');
execFileSync('npm', ['run', 'build'], { cwd: FRONTEND, stdio: 'inherit' });
const env = fs.readFileSync(path.join(FRONTEND, '.env.local'), 'utf8');
if (!/VITE_RUNTIME_ARN=\S+/.test(env)) fail('Refusing to deploy a build without VITE_RUNTIME_ARN: run generate-config first.');

const zip = path.join(FRONTEND, 'dist.zip');
fs.rmSync(zip, { force: true });
execFileSync('zip', ['-r', '-q', zip, '.'], { cwd: path.join(FRONTEND, 'dist'), stdio: 'inherit' });

console.log('📤 Uploading to Amplify...');
const deployment = JSON.parse(execFileSync('aws', [
  'amplify', 'create-deployment', '--app-id', stack.AmplifyAppId, '--branch-name', 'main', '--region', REGION,
], { encoding: 'utf8' }));
execFileSync('curl', ['-sS', '--fail', '-X', 'PUT', '-H', 'Content-Type: application/zip',
  '--data-binary', `@${zip}`, deployment.zipUploadUrl], { stdio: 'inherit' });
execFileSync('aws', ['amplify', 'start-deployment', '--app-id', stack.AmplifyAppId, '--branch-name', 'main',
  '--job-id', deployment.jobId, '--region', REGION], { stdio: 'inherit' });
fs.rmSync(zip, { force: true });
console.log(`✅ Deployment ${deployment.jobId} started: ${stack.AmplifyAppUrl}`);
