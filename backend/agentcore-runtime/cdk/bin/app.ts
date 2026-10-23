#!/usr/bin/env node
import 'source-map-support/register';
import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import { Aspects, Fn } from 'aws-cdk-lib';
import { AwsSolutionsChecks } from 'cdk-nag';
import { AgentCoreInfraStack } from '../lib/infra-stack';
import { AgentCoreRuntimeStack } from '../lib/runtime-stack';
import { sourceTag } from '../lib/source-hash';

const DECIDER_ENDPOINT_NAME = 'voiceshop-decider';   // backend/decider-runtime DECIDER_ENDPOINT_NAME

const app = new cdk.App();
Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  // VOICESHOP_REGION, not CDK_DEFAULT_REGION: the CDK CLI fills CDK_DEFAULT_REGION
  // from AWS_REGION, so a globally set AWS_REGION would silently redirect the deploy.
  region: process.env.VOICESHOP_REGION || 'us-east-1',
};

const actThreshold = Number(app.node.tryGetContext('actThreshold'));
const askThreshold = Number(app.node.tryGetContext('askThreshold'));
if (!(0 < askThreshold && askThreshold < actThreshold && actThreshold <= 1)) {
  console.error('Set actThreshold and askThreshold in cdk.json so that 0 < askThreshold < actThreshold <= 1.');
  throw new Error(`Invalid thresholds: askThreshold=${askThreshold}, actThreshold=${actThreshold}`);
}
const sonicModelId: string = app.node.tryGetContext('sonicModelId') || 'amazon.nova-2-5-sonic';
const imageTag = sourceTag(path.join(__dirname, '../../agent'));

// Sibling CDK app: the gateway comes from its exports.
const infra = new AgentCoreInfraStack(app, 'VoiceShop-AgentCoreInfraStack', {
  env,
  description: 'ECR, ARM64 CodeBuild image build, AgentCore Memory and the runtime role for the VoiceShop session host.',
  imageTag,
  sonicModelId,
  // The endpoint's name is fixed, so its ARN is known without importing it: the
  // session host stacks can deploy while the decider endpoint is still being created.
  deciderEndpointArn: `arn:${cdk.Aws.PARTITION}:sagemaker:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:endpoint/${DECIDER_ENDPOINT_NAME}`,
  gatewayArn: Fn.importValue('VoiceShop-AgentCoreGatewayStack-GatewayArn'),
});

const runtime = new AgentCoreRuntimeStack(app, 'VoiceShop-AgentCoreRuntimeStack', {
  env,
  description: 'VoiceShop session host on AgentCore Runtime (WebSocket over HTTP protocol).',
  repository: infra.repository,
  imageTag,
  runtimeRole: infra.runtimeRole,
  memoryId: infra.memory.attrMemoryId,
  gatewayUrl: Fn.importValue('VoiceShop-AgentCoreGatewayStack-GatewayUrl'),
  deciderEndpointName: DECIDER_ENDPOINT_NAME,
  userPoolId: Fn.importValue('VoiceShop-CognitoStack-UserPoolId'),
  userPoolClientId: Fn.importValue('VoiceShop-CognitoStack-UserPoolClientId'),
  sonicModelId,
  actThreshold,
  askThreshold,
});
runtime.addStackDependency(infra);

cdk.Tags.of(app).add('Project', 'VoiceShop');
cdk.Tags.of(app).add('ManagedBy', 'CDK');
