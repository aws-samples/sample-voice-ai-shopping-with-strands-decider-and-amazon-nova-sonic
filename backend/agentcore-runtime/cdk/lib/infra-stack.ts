import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as bedrockagentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as codebuild from 'aws-cdk-lib/aws-codebuild';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3assets from 'aws-cdk-lib/aws-s3-assets';
import { NagSuppressions } from 'cdk-nag';

export interface AgentCoreInfraStackProps extends cdk.StackProps {
  readonly imageTag: string;
  readonly sonicModelId: string;
  readonly deciderEndpointArn: string;
  readonly gatewayArn: string;
}

/**
 * VoiceShop-AgentCoreInfraStack: everything the session host runtime needs before
 * it can be created. ECR, an ARM64 CodeBuild image build (no local Docker),
 * AgentCore Memory with the user preference strategy, and the runtime role scoped
 * with least privilege.
 */
export class AgentCoreInfraStack extends cdk.Stack {
  public readonly repository: ecr.Repository;
  public readonly buildProject: codebuild.Project;
  public readonly runtimeRole: iam.Role;
  public readonly memory: bedrockagentcore.CfnMemory;

  constructor(scope: Construct, id: string, props: AgentCoreInfraStackProps) {
    super(scope, id, props);
    const region = cdk.Aws.REGION;
    const account = cdk.Aws.ACCOUNT_ID;
    const partition = cdk.Aws.PARTITION;

    this.repository = new ecr.Repository(this, 'SessionHostRepository', {
      repositoryName: 'voiceshop-session-host',
      imageScanOnPush: true,
      // Tags are content hashes and the builds skip a tag that exists: none is ever rewritten.
      imageTagMutability: ecr.TagMutability.IMMUTABLE,
      emptyOnDelete: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const source = new s3assets.Asset(this, 'SessionHostSource', {
      path: '../agent',
      exclude: ['tests', '__pycache__', '.pytest_cache', '*.pyc'],
    });
    this.buildProject = new codebuild.Project(this, 'SessionHostBuild', {
      projectName: 'VoiceShop-SessionHostBuild',
      description: 'ARM64 image build for the VoiceShop session host.',
      source: codebuild.Source.s3({ bucket: source.bucket, path: source.s3ObjectKey }),
      environment: {
        buildImage: codebuild.LinuxArmBuildImage.AMAZON_LINUX_2023_STANDARD_3_0,
        computeType: codebuild.ComputeType.SMALL,
        privileged: true,
      },
      environmentVariables: {
        ECR_REPOSITORY_URI: { value: this.repository.repositoryUri },
        IMAGE_TAG: { value: props.imageTag },
      },
      buildSpec: codebuild.BuildSpec.fromSourceFilename('buildspec.yml'),
      timeout: cdk.Duration.minutes(30),
    });
    this.repository.grantPullPush(this.buildProject);
    this.buildProject.addToRolePolicy(new iam.PolicyStatement({ actions: ['ecr:GetAuthorizationToken'], resources: ['*'] }));
    this.buildProject.addToRolePolicy(new iam.PolicyStatement({ actions: ['ecr:DescribeImages'], resources: [this.repository.repositoryArn] }));

    this.memory = new bedrockagentcore.CfnMemory(this, 'Memory', {
      name: 'VoiceShop_Memory',
      description: 'Shopper preferences across sessions.',
      eventExpiryDuration: 30,
      memoryStrategies: [{
        userPreferenceMemoryStrategy: {
          name: 'ShopperPreferences',
          description: 'Who the shopper buys for, styles, categories, budgets and liked items.',
          namespaces: ['/voiceshop/{actorId}/preferences'],
        },
      }],
    });

    // ---- Runtime role
    this.runtimeRole = new iam.Role(this, 'SessionHostRuntimeRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
        conditions: { StringEquals: { 'aws:SourceAccount': account } },
      }),
      description: 'VoiceShop session host: Sonic, decider endpoint, Gateway, Memory, Sessions table, logs.',
    });
    const allow = (sid: string, actions: string[], resources: string[], conditions?: Record<string, unknown>) =>
      this.runtimeRole.addToPolicy(new iam.PolicyStatement({ sid, actions, resources, conditions }));
    allow('EcrToken', ['ecr:GetAuthorizationToken'], ['*']);
    allow('EcrPull', ['ecr:BatchGetImage', 'ecr:GetDownloadUrlForLayer', 'ecr:BatchCheckLayerAvailability'], [this.repository.repositoryArn]);
    allow('Logs', ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogStreams'],
      [`arn:${partition}:logs:${region}:${account}:log-group:/aws/bedrock-agentcore/runtimes/*`]);
    allow('LogGroups', ['logs:DescribeLogGroups'], [`arn:${partition}:logs:${region}:${account}:log-group:*`]);
    allow('XRay', ['xray:PutTraceSegments', 'xray:PutTelemetryRecords', 'xray:GetSamplingRules', 'xray:GetSamplingTargets'], ['*']);
    allow('AgentCoreMetrics', ['cloudwatch:PutMetricData'], ['*'], { StringEquals: { 'cloudwatch:namespace': 'bedrock-agentcore' } });
    // Bedrock authorizes the bidirectional stream as bedrock:InvokeModel (AccessDenied without it, 2026-10-18).
    allow('Sonic', ['bedrock:InvokeModel', 'bedrock:InvokeModelWithBidirectionalStream'],
      [`arn:${partition}:bedrock:${region}::foundation-model/${props.sonicModelId}`]);
    allow('Decider', ['sagemaker:InvokeEndpoint'], [props.deciderEndpointArn]);
    allow('Gateway', ['bedrock-agentcore:InvokeGateway'], [props.gatewayArn]);
    allow('Memory', ['bedrock-agentcore:CreateEvent', 'bedrock-agentcore:RetrieveMemoryRecords', 'bedrock-agentcore:ListEvents'],
      [this.memory.attrMemoryArn]);
    allow('Sessions', ['dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:UpdateItem', 'dynamodb:Query'],
      [`arn:${partition}:dynamodb:${region}:${account}:table/VoiceShop-Sessions`]);
    allow('CatalogMeta', ['dynamodb:GetItem'], [`arn:${partition}:dynamodb:${region}:${account}:table/VoiceShop-Products`]);
    allow('WorkloadIdentity', ['bedrock-agentcore:GetWorkloadAccessToken', 'bedrock-agentcore:GetWorkloadAccessTokenForJWT',
      'bedrock-agentcore:GetWorkloadAccessTokenForUserId'],
    [`arn:${partition}:bedrock-agentcore:${region}:${account}:workload-identity-directory/default`,
      `arn:${partition}:bedrock-agentcore:${region}:${account}:workload-identity-directory/default/workload-identity/*`]);

    NagSuppressions.addResourceSuppressions([this.runtimeRole, this.buildProject], [
      {
        id: 'AwsSolutions-IAM5',
        reason: 'ecr:GetAuthorizationToken and X-Ray have no resource-level scoping; logs are limited to the AgentCore runtimes prefix; workload identities and the CodeBuild-generated log/report statements use the wildcards those services require. Model, endpoint, gateway, memory and tables are named ARNs.',
      },
      { id: 'AwsSolutions-CB4', reason: 'The image build produces no CodeBuild artifacts (it pushes to ECR).' },
    ], true);

    new cdk.CfnOutput(this, 'RepositoryUri', { value: this.repository.repositoryUri });
    new cdk.CfnOutput(this, 'MemoryId', { value: this.memory.attrMemoryId });
  }
}
