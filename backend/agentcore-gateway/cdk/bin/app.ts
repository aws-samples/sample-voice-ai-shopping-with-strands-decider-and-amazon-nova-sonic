#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { Aspects, Fn } from 'aws-cdk-lib';
import { AwsSolutionsChecks } from 'cdk-nag';
import { GatewayStack } from '../lib/gateway-stack';

const app = new cdk.App();
Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  // VOICESHOP_REGION, not CDK_DEFAULT_REGION: the CDK CLI fills CDK_DEFAULT_REGION
  // from AWS_REGION, so a globally set AWS_REGION would silently redirect the deploy.
  region: process.env.VOICESHOP_REGION || 'us-east-1',
};

// Sibling CDK app of backend-infrastructure: the tool Lambdas come from its exports.
new GatewayStack(app, 'VoiceShop-AgentCoreGatewayStack', {
  env,
  description: 'AgentCore Gateway (MCP, IAM auth) exposing the five VoiceShop tool Lambdas to the session host.',
  toolFunctionArns: {
    search_catalog: Fn.importValue('VoiceShop-LambdaStack-SearchCatalogFunctionArn'),
    get_product_detail: Fn.importValue('VoiceShop-LambdaStack-GetProductDetailFunctionArn'),
    add_to_cart: Fn.importValue('VoiceShop-LambdaStack-AddToCartFunctionArn'),
    remove_from_cart: Fn.importValue('VoiceShop-LambdaStack-RemoveFromCartFunctionArn'),
    place_order: Fn.importValue('VoiceShop-LambdaStack-PlaceOrderFunctionArn'),
  },
});

cdk.Tags.of(app).add('Project', 'VoiceShop');
cdk.Tags.of(app).add('ManagedBy', 'CDK');
