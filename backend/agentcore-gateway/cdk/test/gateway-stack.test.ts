import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { GatewayStack } from '../lib/gateway-stack';

const arn = (name: string) => `arn:aws:lambda:us-east-1:111111111111:function:VoiceShop-${name}`;
const template = Template.fromStack(new GatewayStack(new cdk.App(), 'TestGateway', {
  env: { account: '111111111111', region: 'us-east-1' },
  toolFunctionArns: {
    search_catalog: arn('SearchCatalog'),
    get_product_detail: arn('GetProductDetail'),
    add_to_cart: arn('AddToCart'),
    remove_from_cart: arn('RemoveFromCart'),
    place_order: arn('PlaceOrder'),
  },
}));

test('MCP gateway with AWS_IAM inbound auth', () => {
  template.hasResourceProperties('AWS::BedrockAgentCore::Gateway', { ProtocolType: 'MCP', AuthorizerType: 'AWS_IAM' });
});

test('exactly five Lambda targets, one per tool', () => {
  const names = Object.values(template.findResources('AWS::BedrockAgentCore::GatewayTarget')).map((t) => t.Properties.Name).sort();
  expect(names).toEqual(['add-to-cart', 'get-product-detail', 'place-order', 'remove-from-cart', 'search-catalog']);
});

test('gateway role may only invoke the five tool functions', () => {
  const statements = Object.values(template.findResources('AWS::IAM::Policy'))
    .flatMap((p) => p.Properties.PolicyDocument.Statement);
  const actions = new Set(statements.flatMap((s) => [].concat(s.Action)));
  expect(actions).toEqual(new Set(['lambda:InvokeFunction']));
  const resources = JSON.stringify(statements.flatMap((s) => [].concat(s.Resource)));
  for (const fn of ['SearchCatalog', 'GetProductDetail', 'AddToCart', 'RemoveFromCart', 'PlaceOrder']) expect(resources).toContain(`VoiceShop-${fn}`);
  expect(resources).not.toContain('"*"');
});
