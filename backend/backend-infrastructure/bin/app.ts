#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { Aspects } from 'aws-cdk-lib';
import { AwsSolutionsChecks } from 'cdk-nag';
import { NetworkStack } from '../lib/network-stack';
import { DynamoDBStack } from '../lib/dynamodb-stack';
import { StorageStack } from '../lib/storage-stack';
import { SearchStack } from '../lib/search-stack';
import { LambdaStack } from '../lib/lambda-stack';
import { CognitoStack } from '../lib/cognito-stack';

const app = new cdk.App();
Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  // VOICESHOP_REGION, not CDK_DEFAULT_REGION: the CDK CLI fills CDK_DEFAULT_REGION
  // from AWS_REGION, so a globally set AWS_REGION would silently redirect the deploy.
  region: process.env.VOICESHOP_REGION || 'us-east-1',
};

// One Availability Zone in this sample; use two or more in production (ADR-32).
const networkStack = new NetworkStack(app, 'VoiceShop-NetworkStack', {
  env,
  description: 'Private network for catalog search: one isolated subnet, S3 and Bedrock runtime endpoints, no internet route.',
  availabilityZone: process.env.VOICESHOP_AZ || `${env.region}a`,
});

const dynamoDbStack = new DynamoDBStack(app, 'VoiceShop-DynamoDBStack', {
  env,
  description: 'VoiceShop Products, Sessions, Cart and Orders tables.',
});

const storageStack = new StorageStack(app, 'VoiceShop-StorageStack', {
  env,
  description: 'Private product images bucket (presigned URLs only) and its access log bucket.',
});

const searchStack = new SearchStack(app, 'VoiceShop-SearchStack', {
  env,
  description: 'OpenSearch Serverless vector collection for the catalog, reachable only through its VPC endpoint.',
  vpc: networkStack.vpc,
  subnets: networkStack.subnets,
});
searchStack.addStackDependency(networkStack);

const lambdaStack = new LambdaStack(app, 'VoiceShop-LambdaStack', {
  env,
  description: 'search_catalog, get_product_detail, add_to_cart and place_order tool Lambdas (AgentCore Gateway targets), and the catalog index writer.',
  productsTable: dynamoDbStack.productsTable,
  cartTable: dynamoDbStack.cartTable,
  ordersTable: dynamoDbStack.ordersTable,
  imagesBucket: storageStack.imagesBucket,
  collection: searchStack.collection,
  vpc: networkStack.vpc,
  subnets: networkStack.subnets,
});
lambdaStack.addStackDependency(dynamoDbStack);
lambdaStack.addStackDependency(storageStack);
lambdaStack.addStackDependency(searchStack);

new CognitoStack(app, 'VoiceShop-CognitoStack', {
  env,
  description: 'Cognito User Pool, Identity Pool and demo test user; the authenticated role may only open the session host WebSocket.',
});

cdk.Tags.of(app).add('Project', 'VoiceShop');
cdk.Tags.of(app).add('ManagedBy', 'CDK');
