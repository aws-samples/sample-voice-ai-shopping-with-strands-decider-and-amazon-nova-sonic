import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { NetworkStack } from '../lib/network-stack';
import { DynamoDBStack } from '../lib/dynamodb-stack';
import { StorageStack } from '../lib/storage-stack';
import { SearchStack } from '../lib/search-stack';
import { LambdaStack } from '../lib/lambda-stack';
import { CognitoStack } from '../lib/cognito-stack';

// Same wiring as bin/app.ts, built once for all assertions (LambdaStack bundles
// six functions with esbuild, which takes a few seconds).
const env = { account: '111111111111', region: 'us-east-1' };
// cdk.json's feature flags matter here (for example S3 server access logs via
// bucket policy), so the test app loads the same context as `cdk synth`.
const app = new cdk.App({ context: require('../cdk.json').context });
const network = new NetworkStack(app, 'Network', { env, availabilityZone: 'us-east-1a' });
const dynamo = new DynamoDBStack(app, 'DynamoDB', { env });
const storage = new StorageStack(app, 'Storage', { env });
const search = new SearchStack(app, 'Search', { env, vpc: network.vpc, subnets: network.subnets });
const lambdas = new LambdaStack(app, 'Lambda', {
  env,
  productsTable: dynamo.productsTable,
  cartTable: dynamo.cartTable,
  ordersTable: dynamo.ordersTable,
  imagesBucket: storage.imagesBucket,
  collection: search.collection,
  vpc: network.vpc,
  subnets: network.subnets,
});
const cognito = new CognitoStack(app, 'Cognito', { env });
const t = {
  network: Template.fromStack(network),
  dynamo: Template.fromStack(dynamo),
  storage: Template.fromStack(storage),
  search: Template.fromStack(search),
  lambdas: Template.fromStack(lambdas),
  cognito: Template.fromStack(cognito),
};

/** Every IAM action granted by a stack's policies (inline and managed-by-CDK). */
function actionsOf(template: Template): string[] {
  const actions: string[] = [];
  for (const resource of Object.values(template.findResources('AWS::IAM::Policy'))) {
    for (const statement of resource.Properties.PolicyDocument.Statement) {
      actions.push(...[].concat(statement.Action));
    }
  }
  return actions;
}

describe('DynamoDBStack', () => {
  test('four on-demand tables, destroyed with the stack', () => {
    t.dynamo.resourceCountIs('AWS::DynamoDB::Table', 4);
    t.dynamo.allResourcesProperties('AWS::DynamoDB::Table', { BillingMode: 'PAY_PER_REQUEST' });
    for (const table of Object.values(t.dynamo.findResources('AWS::DynamoDB::Table'))) {
      expect(table.DeletionPolicy).toBe('Delete');
    }
  });

  test('Products has no secondary index; Sessions and Cart expire on expiresAt', () => {
    const products = Object.values(t.dynamo.findResources('AWS::DynamoDB::Table'))
      .find((r) => r.Properties.TableName === 'VoiceShop-Products')!;
    expect(products.Properties.GlobalSecondaryIndexes).toBeUndefined();
    for (const name of ['VoiceShop-Sessions', 'VoiceShop-Cart']) {
      t.dynamo.hasResourceProperties('AWS::DynamoDB::Table', {
        TableName: name,
        TimeToLiveSpecification: { AttributeName: 'expiresAt', Enabled: true },
      });
    }
  });
});

describe('StorageStack', () => {
  test('images bucket is private, encrypted, logged and SSL-only', () => {
    t.storage.hasResourceProperties('AWS::S3::Bucket', {
      BucketName: Match.stringLikeRegexp('voiceshop-images'),
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true, BlockPublicPolicy: true, IgnorePublicAcls: true, RestrictPublicBuckets: true,
      },
      BucketEncryption: Match.anyValue(),
      LoggingConfiguration: Match.objectLike({ LogFilePrefix: 'images/' }),
    });
    t.storage.hasResourceProperties('AWS::S3::BucketPolicy', {
      PolicyDocument: Match.objectLike({
        Statement: Match.arrayWith([Match.objectLike({
          Effect: 'Deny', Condition: { Bool: { 'aws:SecureTransport': 'false' } },
        })]),
      }),
    });
  });
});

describe('NetworkStack', () => {
  test('one isolated subnet in one zone, no NAT gateway or internet gateway', () => {
    t.network.resourceCountIs('AWS::EC2::Subnet', 1);
    t.network.hasResourceProperties('AWS::EC2::Subnet', { AvailabilityZone: 'us-east-1a', MapPublicIpOnLaunch: false });
    t.network.resourceCountIs('AWS::EC2::NatGateway', 0);
    t.network.resourceCountIs('AWS::EC2::InternetGateway', 0);
  });

  test('S3 gateway endpoint and Bedrock runtime interface endpoint, with flow logs', () => {
    const endpoints = Object.values(t.network.findResources('AWS::EC2::VPCEndpoint'));
    expect(endpoints.map((e) => e.Properties.VpcEndpointType ?? 'Gateway').sort()).toEqual(['Gateway', 'Interface']);
    expect(JSON.stringify(endpoints)).toContain('bedrock-runtime');
    t.network.resourceCountIs('AWS::EC2::FlowLog', 1);
  });
});

describe('SearchStack', () => {
  test('vector collection with standby replicas off, reachable only through its VPC endpoint', () => {
    t.search.hasResourceProperties('AWS::OpenSearchServerless::Collection', {
      Name: 'voiceshop-catalog', Type: 'VECTORSEARCH', StandbyReplicas: 'DISABLED',
    });
    const policy = Object.values(t.search.findResources('AWS::OpenSearchServerless::SecurityPolicy'))
      .find((p) => p.Properties.Type === 'network')!;
    const text = JSON.stringify(policy.Properties.Policy);
    expect(text).toContain('\\"AllowFromPublic\\":false');
    expect(text).toContain('SourceVPCEs');
    expect(text).not.toContain('\\"AllowFromPublic\\":true');
    t.search.resourceCountIs('AWS::OpenSearchServerless::VpcEndpoint', 1);
  });
});

describe('LambdaStack', () => {
  const functions = Object.values(t.lambdas.findResources('AWS::Lambda::Function'))
    .filter((f) => String(f.Properties.FunctionName ?? '').startsWith('VoiceShop-'));
  const byName = (name: string) => functions.find((f) => f.Properties.FunctionName === name)!;

  test('six Node.js 22 functions, each with its own role', () => {
    expect(functions.map((f) => f.Properties.FunctionName).sort()).toEqual([
      'VoiceShop-AddToCart', 'VoiceShop-CatalogIndexWriter', 'VoiceShop-GetProductDetail', 'VoiceShop-PlaceOrder',
      'VoiceShop-RemoveFromCart', 'VoiceShop-SearchCatalog',
    ]);
    for (const fn of functions) expect(fn.Properties.Runtime).toBe('nodejs22.x');
    expect(new Set(functions.map((f) => JSON.stringify(f.Properties.Role))).size).toBe(6);
  });

  test('only the two functions that reach the search collection run in the VPC', () => {
    expect(byName('VoiceShop-SearchCatalog').Properties.VpcConfig).toBeDefined();
    expect(byName('VoiceShop-CatalogIndexWriter').Properties.VpcConfig).toBeDefined();
    for (const name of ['VoiceShop-GetProductDetail', 'VoiceShop-AddToCart', 'VoiceShop-RemoveFromCart', 'VoiceShop-PlaceOrder']) {
      expect(byName(name).Properties.VpcConfig).toBeUndefined();
    }
  });

  test('search-catalog and the index writer get 1,024 MB, the others 512 MB', () => {
    t.lambdas.hasResourceProperties('AWS::Lambda::Function', { FunctionName: 'VoiceShop-SearchCatalog', MemorySize: 1024 });
    t.lambdas.hasResourceProperties('AWS::Lambda::Function', { FunctionName: 'VoiceShop-CatalogIndexWriter', MemorySize: 1024 });
    t.lambdas.hasResourceProperties('AWS::Lambda::Function', { FunctionName: 'VoiceShop-PlaceOrder', MemorySize: 512 });
  });

  test('no wildcard service actions', () => {
    const actions = actionsOf(t.lambdas);
    expect(actions).not.toContain('dynamodb:*');
    expect(actions).not.toContain('s3:*');
    expect(actions).not.toContain('*');
  });

  test('search-catalog may only read the products index; only the index writer may write it', () => {
    const policies = Object.values(t.lambdas.findResources('AWS::OpenSearchServerless::AccessPolicy'))
      .map((p) => ({ name: p.Properties.Name, text: JSON.stringify(p.Properties.Policy) }));
    const read = policies.find((p) => p.name === 'voiceshop-search-read')!;
    const write = policies.find((p) => p.name === 'voiceshop-index-write')!;
    expect(read.text).toContain('index/voiceshop-catalog/products');
    expect(read.text).toContain('aoss:ReadDocument');
    expect(read.text).not.toContain('aoss:WriteDocument');
    expect(write.text).toContain('aoss:WriteDocument');
  });
});

describe('CognitoStack', () => {
  test('the authenticated role may only open the session host WebSocket', () => {
    expect(new Set(actionsOf(t.cognito))).toEqual(new Set(['bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStream']));
  });

  test('a revoked sign-in gets no AWS credentials', () => {
    t.cognito.hasResourceProperties('AWS::Cognito::IdentityPool', {
      AllowUnauthenticatedIdentities: false,
      CognitoIdentityProviders: [Match.objectLike({ ServerSideTokenCheck: true })],
    });
  });
});
