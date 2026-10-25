import * as cdk from 'aws-cdk-lib';
import { Annotations, Match, Template } from 'aws-cdk-lib/assertions';
import { Aspects } from 'aws-cdk-lib';
import { AwsSolutionsChecks } from 'cdk-nag';
import { AgentCoreInfraStack } from '../lib/infra-stack';
import { AgentCoreRuntimeStack } from '../lib/runtime-stack';

const env = { account: '111111111111', region: 'us-east-1' };
const app = new cdk.App();
Aspects.of(app).add(new AwsSolutionsChecks());
const infra = new AgentCoreInfraStack(app, 'TestInfra', {
  env,
  imageTag: 'host-0123456789ab',
  sonicModelId: 'amazon.nova-2-5-sonic',
  deciderEndpointArn: 'arn:aws:sagemaker:us-east-1:111111111111:endpoint/voiceshop-decider',
  gatewayArn: 'arn:aws:bedrock-agentcore:us-east-1:111111111111:gateway/voiceshop-gateway-abcdefghij',
});
const runtime = new AgentCoreRuntimeStack(app, 'TestRuntime', {
  env,
  repository: infra.repository,
  imageTag: 'host-0123456789ab',
  runtimeRole: infra.runtimeRole,
  memoryId: infra.memory.attrMemoryId,
  gatewayUrl: 'https://voiceshop-gateway-abcdefghij.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp',
  deciderEndpointName: 'voiceshop-decider',
  userPoolId: 'us-east-1_TestPool',
  userPoolClientId: 'testclientid',
  sonicModelId: 'amazon.nova-2-5-sonic',
  actThreshold: 0.8,
  askThreshold: 0.5,
});
const infraTemplate = Template.fromStack(infra);
const runtimeTemplate = Template.fromStack(runtime);

const roleStatements = () => Object.values(infraTemplate.findResources('AWS::IAM::Policy'))
  .filter((p) => JSON.stringify(p.Properties.Roles).includes('SessionHostRuntimeRole'))
  .flatMap((p) => p.Properties.PolicyDocument.Statement);

test('runtime uses the HTTP protocol (WebSocket /ws), public network, content-tagged image', () => {
  runtimeTemplate.hasResourceProperties('AWS::BedrockAgentCore::Runtime', {
    ProtocolConfiguration: 'HTTP',
    NetworkConfiguration: { NetworkMode: 'PUBLIC' },
    AgentRuntimeArtifact: { ContainerConfiguration: { ContainerUri: Match.objectLike({ 'Fn::Join': Match.anyValue() }) } },
  });
  expect(JSON.stringify(runtimeTemplate.toJSON())).toContain(':host-0123456789ab');
});

test('runtime environment carries every setting the session host reads', () => {
  const vars = Object.values(runtimeTemplate.findResources('AWS::BedrockAgentCore::Runtime'))[0].Properties.EnvironmentVariables;
  for (const key of ['AGENTCORE_GATEWAY_URL', 'DECIDER_ENDPOINT_NAME', 'SONIC_MODEL_ID', 'MEMORY_ID', 'ACT_THRESHOLD',
    'ASK_THRESHOLD', 'SESSIONS_TABLE_NAME', 'PRODUCTS_TABLE_NAME']) expect(vars).toHaveProperty(key);
  expect(vars.DECIDER_ENDPOINT_NAME).toBe('voiceshop-decider');
});

test('runtime ARN is exported for the frontend config', () => {
  runtimeTemplate.hasOutput('AgentRuntimeArn', { Export: { Name: 'VoiceShop-AgentCoreRuntimeStack-AgentRuntimeArn' } });
});

test('memory has the user preference strategy on the shopper namespace', () => {
  infraTemplate.hasResourceProperties('AWS::BedrockAgentCore::Memory', {
    MemoryStrategies: [{ UserPreferenceMemoryStrategy: Match.objectLike({ Namespaces: ['/voiceshop/{actorId}/preferences'] }) }],
  });
});

test('ECR repository scans on push and is emptied on delete', () => {
  infraTemplate.hasResourceProperties('AWS::ECR::Repository', {
    RepositoryName: 'voiceshop-session-host', ImageScanningConfiguration: { ScanOnPush: true }, EmptyOnDelete: true,
  });
});

test('image build runs on ARM64 CodeBuild with the content tag', () => {
  infraTemplate.hasResourceProperties('AWS::CodeBuild::Project', {
    Environment: Match.objectLike({
      Type: 'ARM_CONTAINER', PrivilegedMode: true,
      EnvironmentVariables: Match.arrayWith([Match.objectLike({ Name: 'IMAGE_TAG', Value: 'host-0123456789ab' })]),
    }),
  });
});

test('runtime role is assumed by AgentCore and scoped to named resources', () => {
  infraTemplate.hasResourceProperties('AWS::IAM::Role', {
    AssumeRolePolicyDocument: { Statement: [Match.objectLike({ Principal: { Service: 'bedrock-agentcore.amazonaws.com' } })] },
  });
  const bySid = Object.fromEntries(roleStatements().map((s) => [s.Sid, s]));
  expect(bySid.Decider.Resource).toBe('arn:aws:sagemaker:us-east-1:111111111111:endpoint/voiceshop-decider');
  expect(bySid.Gateway.Resource).toContain(':gateway/voiceshop-gateway-');
  expect(JSON.stringify(bySid.Sonic.Resource)).toContain('foundation-model/amazon.nova-2-5-sonic');
  expect(JSON.stringify(bySid.Sessions.Resource)).toContain('table/VoiceShop-Sessions');
  // Session state and the per-shopper turn counter, on the Sessions table only.
  expect(([] as string[]).concat(bySid.Sessions.Action).sort()).toEqual(
    ['dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:Query', 'dynamodb:UpdateItem']);
  expect(([] as string[]).concat(bySid.CatalogMeta.Action)).toEqual(['dynamodb:GetItem']);
  // The session host never writes cart or orders directly: those go through the Gateway tools.
  const actions = roleStatements().flatMap((s) => ([] as string[]).concat(s.Action));
  expect(actions).not.toContain('lambda:InvokeFunction');
  expect(actions.filter((a) => a.startsWith('dynamodb:'))).not.toContain('dynamodb:DeleteItem');
});

test('cdk-nag reports no unsuppressed errors', () => {
  for (const stack of [infra, runtime]) {
    expect(Annotations.fromStack(stack).findError('*', Match.stringLikeRegexp('AwsSolutions-.*'))).toEqual([]);
  }
});
