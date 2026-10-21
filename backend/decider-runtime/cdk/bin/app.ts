#!/usr/bin/env node
import 'source-map-support/register';
import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import { Aspects } from 'aws-cdk-lib';
import { AwsSolutionsChecks } from 'cdk-nag';
import { DeciderImageStack } from '../lib/decider-image-stack';
import { DeciderInfraStack } from '../lib/decider-infra-stack';
import { DeciderEndpointStack } from '../lib/decider-endpoint-stack';
import { deciderImageTag } from '../lib/image-tag';

const app = new cdk.App();
Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  // VOICESHOP_REGION, not CDK_DEFAULT_REGION: the CDK CLI fills CDK_DEFAULT_REGION
  // from AWS_REGION, so a globally set AWS_REGION would silently redirect the deploy.
  region: process.env.VOICESHOP_REGION || 'us-east-1',
};

const image = new DeciderImageStack(app, 'VoiceShop-DeciderImageStack', {
  env,
  description: 'ECR repository for the Strands Decider image (kept by cleanup-all.sh --keep-decider-image).',
});
const infra = new DeciderInfraStack(app, 'VoiceShop-DeciderInfraStack', {
  env,
  description: 'x86_64 CodeBuild image build, model bucket and model upload for the Strands Decider endpoint (ADR-22).',
  repository: image.repository,
});

const minInstances = Number(app.node.tryGetContext('deciderMinInstances'));
const maxInstances = Number(app.node.tryGetContext('deciderMaxInstances'));
if (!Number.isInteger(minInstances) || minInstances < 1 || !Number.isInteger(maxInstances) || maxInstances < minInstances) {
  throw new Error(`deciderMinInstances (${minInstances}) must be >= 1 and <= deciderMaxInstances (${maxInstances})`);
}

const endpoint = new DeciderEndpointStack(app, 'VoiceShop-DeciderEndpointStack', {
  env,
  description: 'Strands Decider 2B on a SageMaker real-time GPU endpoint with auto-scaling (ADR-22).',
  repository: infra.repository,
  modelBucket: infra.modelBucket,
  imageTag: app.node.tryGetContext('deciderImageTag') || deciderImageTag(path.join(__dirname, '../../container')),
  instanceType: app.node.tryGetContext('deciderInstanceType') || 'g5.xlarge',
  minInstances,
  maxInstances,
  invocationsPerInstanceTarget: Number(app.node.tryGetContext('deciderInvocationsPerInstanceTarget') || 120),
});
endpoint.addStackDependency(infra);

cdk.Tags.of(app).add('Project', 'VoiceShop');
cdk.Tags.of(app).add('ManagedBy', 'CDK');
