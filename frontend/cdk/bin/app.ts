#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { Aspects } from 'aws-cdk-lib';
import { AwsSolutionsChecks } from 'cdk-nag';
import { FrontendStack } from '../lib/frontend-stack';

const app = new cdk.App();
Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  // VOICESHOP_REGION, not CDK_DEFAULT_REGION: the CDK CLI fills CDK_DEFAULT_REGION
  // from AWS_REGION, so a globally set AWS_REGION would silently redirect the deploy.
  region: process.env.VOICESHOP_REGION || 'us-east-1',
};

new FrontendStack(app, 'VoiceShop-FrontendStack', {
  env,
  description: 'VoiceShop shopping UI hosted on AWS Amplify (manual deployments).',
});

cdk.Tags.of(app).add('Project', 'VoiceShop');
cdk.Tags.of(app).add('ManagedBy', 'CDK');
