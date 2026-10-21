#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { Aspects, Fn } from 'aws-cdk-lib';
import { AwsSolutionsChecks } from 'cdk-nag';
import { CatalogDataStack } from '../lib/catalog-data-stack';

const app = new cdk.App();
Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  // VOICESHOP_REGION, not CDK_DEFAULT_REGION: the CDK CLI fills CDK_DEFAULT_REGION
  // from AWS_REGION, so a globally set AWS_REGION would silently redirect the deploy.
  region: process.env.VOICESHOP_REGION || 'us-east-1',
};

// Sibling CDK app of backend-infrastructure: the index writer and images bucket
// come from that app's exports.
new CatalogDataStack(app, 'VoiceShop-CatalogDataStack', {
  env,
  description: 'Loads the synthetic catalog (VoiceShop-CatalogCuration CodeBuild project) into S3 and DynamoDB, and the search index through the index writer.',
  indexWriterFunctionArn: Fn.importValue('VoiceShop-LambdaStack-CatalogIndexWriterFunctionArn'),
  imagesBucketName: Fn.importValue('VoiceShop-StorageStack-ImagesBucketName'),
});

cdk.Tags.of(app).add('Project', 'VoiceShop');
cdk.Tags.of(app).add('ManagedBy', 'CDK');
