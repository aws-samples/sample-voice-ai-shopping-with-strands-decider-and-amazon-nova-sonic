import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { DeciderImageStack } from '../lib/decider-image-stack';
import { DeciderInfraStack } from '../lib/decider-infra-stack';
import { DeciderEndpointStack } from '../lib/decider-endpoint-stack';

const env = { account: '111111111111', region: 'us-east-1' };
const app = new cdk.App({ context: require('../cdk.json').context });
const image = new DeciderImageStack(app, 'Image', { env });
const infra = new DeciderInfraStack(app, 'Infra', { env, repository: image.repository });
const endpoint = new DeciderEndpointStack(app, 'Endpoint', {
  env,
  repository: infra.repository,
  modelBucket: infra.modelBucket,
  imageTag: 'sm-test',
  instanceType: 'g5.xlarge',
  minInstances: 1,
  maxInstances: 3,
  invocationsPerInstanceTarget: 120,
});
const infraTemplate = Template.fromStack(infra);
const imageTemplate = Template.fromStack(image);
const t = Template.fromStack(endpoint);

test('ECR scans on push and the model bucket is private', () => {
  imageTemplate.hasResourceProperties('AWS::ECR::Repository', { ImageScanningConfiguration: { ScanOnPush: true } });
  infraTemplate.resourceCountIs('AWS::ECR::Repository', 0);   // its own stack, so cleanup can keep it
  infraTemplate.hasResourceProperties('AWS::S3::Bucket', {
    PublicAccessBlockConfiguration: { BlockPublicAcls: true, BlockPublicPolicy: true, IgnorePublicAcls: true, RestrictPublicBuckets: true },
  });
});

test('model runs with network isolation and no VPC, weights mounted from model/', () => {
  t.hasResourceProperties('AWS::SageMaker::Model', {
    EnableNetworkIsolation: true,
    VpcConfig: Match.absent(),
    PrimaryContainer: Match.objectLike({
      ModelDataSource: { S3DataSource: Match.objectLike({ S3DataType: 'S3Prefix', CompressionType: 'None' }) },
    }),
  });
});

test('endpoint uses ml.g5.xlarge with the minimum warm and long startup timeouts', () => {
  t.hasResourceProperties('AWS::SageMaker::EndpointConfig', {
    ProductionVariants: [Match.objectLike({
      InstanceType: 'ml.g5.xlarge', InitialInstanceCount: 1, ContainerStartupHealthCheckTimeoutInSeconds: 1800,
    })],
  });
  t.hasResourceProperties('AWS::SageMaker::Endpoint', { EndpointName: 'voiceshop-decider' });
});

test('auto-scaling between min and max on invocations per instance', () => {
  t.hasResourceProperties('AWS::ApplicationAutoScaling::ScalableTarget', {
    MinCapacity: 1, MaxCapacity: 3, ScalableDimension: 'sagemaker:variant:DesiredInstanceCount',
  });
  t.hasResourceProperties('AWS::ApplicationAutoScaling::ScalingPolicy', {
    TargetTrackingScalingPolicyConfiguration: Match.objectLike({
      TargetValue: 120,
      PredefinedMetricSpecification: { PredefinedMetricType: 'SageMakerVariantInvocationsPerInstance' },
    }),
  });
});

test('execution role reads only the model/ prefix; nothing gets s3:* or sagemaker:*', () => {
  const policies = JSON.stringify(t.findResources('AWS::IAM::Policy'));
  expect(policies).toContain('/model/*');
  expect(policies).not.toContain('"s3:*"');
  expect(policies).not.toContain('"sagemaker:*"');
});

test('no VPC resources and no ec2 permissions', () => {
  t.resourceCountIs('AWS::EC2::SecurityGroup', 0);
  expect(JSON.stringify(t.findResources('AWS::IAM::Policy'))).not.toContain('"ec2:');
});


test('the model upload runs in CodeBuild and writes only the model bucket', () => {
  infraTemplate.hasResourceProperties('AWS::CodeBuild::Project', { Name: 'VoiceShop-DeciderModelUpload' });
  const policies = JSON.stringify(infraTemplate.findResources('AWS::IAM::Policy'));
  expect(policies).not.toContain('"s3:*"');
});


test('endpoint runs on the GPU host image with driver 550 (CUDA 12.4)', () => {
  t.hasResourceProperties('AWS::SageMaker::EndpointConfig', {
    ProductionVariants: [Match.objectLike({ InferenceAmiVersion: 'al2-ami-sagemaker-inference-gpu-3-1' })],
  });
});
