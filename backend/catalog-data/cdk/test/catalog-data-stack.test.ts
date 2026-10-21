import * as cdk from 'aws-cdk-lib';
import { Annotations, Match, Template } from 'aws-cdk-lib/assertions';
import { Aspects } from 'aws-cdk-lib';
import { AwsSolutionsChecks } from 'cdk-nag';
import { CatalogDataStack } from '../lib/catalog-data-stack';

const app = new cdk.App();
Aspects.of(app).add(new AwsSolutionsChecks());
const stack = new CatalogDataStack(app, 'Catalog', {
  env: { account: '111111111111', region: 'us-east-1' },
  indexWriterFunctionArn: 'arn:aws:lambda:us-east-1:111111111111:function:VoiceShop-CatalogIndexWriter',
  imagesBucketName: 'voiceshop-images-111111111111-us-east-1',
});
const t = Template.fromStack(stack);

test('one curation project, outside any VPC, no extra buckets', () => {
  t.resourceCountIs('AWS::CodeBuild::Project', 1);
  t.hasResourceProperties('AWS::CodeBuild::Project', { Name: 'VoiceShop-CatalogCuration', VpcConfig: Match.absent() });
  t.resourceCountIs('AWS::S3::Bucket', 0);
  t.resourceCountIs('AWS::EC2::SecurityGroup', 0);
});

test('images access is limited to the products/ and catalog-index/ prefixes', () => {
  const policies = JSON.stringify(t.findResources('AWS::IAM::Policy'));
  expect(policies).toContain('voiceshop-images-111111111111-us-east-1/products/*');
  expect(policies).toContain('voiceshop-images-111111111111-us-east-1/catalog-index/*');
  expect(policies).not.toContain('"s3:*"');
});

test('the index is written only through the index writer: no OpenSearch access of its own', () => {
  const policies = JSON.stringify(t.findResources('AWS::IAM::Policy'));
  expect(policies).toContain('lambda:InvokeFunction');
  expect(policies).toContain('function:VoiceShop-CatalogIndexWriter');
  expect(policies).not.toContain('aoss:');
  t.resourceCountIs('AWS::OpenSearchServerless::AccessPolicy', 0);
});

test('cdk-nag reports no unsuppressed errors', () => {
  expect(Annotations.fromStack(stack).findError('*', Match.stringLikeRegexp('AwsSolutions-.*'))).toEqual([]);
});
