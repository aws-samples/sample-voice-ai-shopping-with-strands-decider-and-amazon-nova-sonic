import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as appscaling from 'aws-cdk-lib/aws-applicationautoscaling';
import * as codebuild from 'aws-cdk-lib/aws-codebuild';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3assets from 'aws-cdk-lib/aws-s3-assets';
import * as sagemaker from 'aws-cdk-lib/aws-sagemaker';
import { NagSuppressions } from 'cdk-nag';
import { MODEL_DATA_PREFIX } from './decider-infra-stack';

export const DECIDER_ENDPOINT_NAME = 'voiceshop-decider';
const VARIANT_NAME = 'AllTraffic';

export interface DeciderEndpointStackProps extends cdk.StackProps {
  readonly repository: ecr.IRepository;
  readonly modelBucket: s3.IBucket;
  readonly imageTag: string;
  readonly instanceType: string;
  readonly minInstances: number;
  readonly maxInstances: number;
  /** Target InvokeEndpoint calls per instance per minute for auto-scaling. */
  readonly invocationsPerInstanceTarget: number;
}

/**
 * The Strands Decider as a SageMaker real-time endpoint (ADR-22): one GPU
 * instance minimum, scaled out on invocations per instance. The container runs
 * with network isolation (no network access at all); SageMaker mounts the
 * weights from the model bucket at /opt/ml/model before it starts (ADR-26: no VPC).
 */
export class DeciderEndpointStack extends cdk.Stack {
  public readonly endpointName = DECIDER_ENDPOINT_NAME;
  public readonly endpointArn: string;

  constructor(scope: Construct, id: string, props: DeciderEndpointStackProps) {
    super(scope, id, props);

    // ---- Execution role (what SageMaker uses to pull the image, read the model and log)
    const executionRole = new iam.Role(this, 'DeciderExecutionRole', {
      assumedBy: new iam.ServicePrincipal('sagemaker.amazonaws.com'),
      description: 'SageMaker execution role for the VoiceShop decider endpoint.',
    });
    props.repository.grantPull(executionRole);
    executionRole.addToPolicy(new iam.PolicyStatement({ actions: ['ecr:GetAuthorizationToken'], resources: ['*'] }));
    executionRole.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject'],
      resources: [props.modelBucket.arnForObjects(`${MODEL_DATA_PREFIX}*`)],
    }));
    executionRole.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:ListBucket'],
      resources: [props.modelBucket.bucketArn],
      conditions: { StringLike: { 's3:prefix': [`${MODEL_DATA_PREFIX}*`] } },
    }));
    executionRole.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogStreams'],
      resources: [`arn:${cdk.Aws.PARTITION}:logs:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:log-group:/aws/sagemaker/Endpoints/${DECIDER_ENDPOINT_NAME}*`],
    }));
    executionRole.addToPolicy(new iam.PolicyStatement({
      actions: ['cloudwatch:PutMetricData'],
      resources: ['*'],
      conditions: { StringEquals: { 'cloudwatch:namespace': ['/aws/sagemaker/Endpoints', 'AWS/SageMaker'] } },
    }));

    // ---- Model, endpoint config, endpoint
    const model = new sagemaker.CfnModel(this, 'DeciderModel', {
      executionRoleArn: executionRole.roleArn,
      enableNetworkIsolation: true,
      primaryContainer: {
        image: `${props.repository.repositoryUri}:${props.imageTag}`,
        modelDataSource: {
          s3DataSource: {
            s3Uri: `s3://${props.modelBucket.bucketName}/${MODEL_DATA_PREFIX}`,
            s3DataType: 'S3Prefix',
            compressionType: 'None',
          },
        },
      },
    });
    // The role's policies must exist before SageMaker validates the model.
    model.node.addDependency(executionRole);

    const endpointConfig = new sagemaker.CfnEndpointConfig(this, 'DeciderEndpointConfig', {
      productionVariants: [{
        variantName: VARIANT_NAME,
        modelName: model.attrModelName,
        instanceType: `ml.${props.instanceType}`,
        initialInstanceCount: props.minInstances,
        // The default GPU host image has NVIDIA driver 470 (CUDA 11.4): torch's CUDA 12.6
        // libraries refused it ("driver too old", 2026-10-21). This one has driver 550 and
        // CUDA 12.4, with the host's CUDA-compat mounting disabled.
        inferenceAmiVersion: 'al2-ami-sagemaker-inference-gpu-3-1',
        // Model data (~4.8 GB) download + load before /ping answers.
        modelDataDownloadTimeoutInSeconds: 1800,
        containerStartupHealthCheckTimeoutInSeconds: 1800,
      }],
    });

    const endpoint = new sagemaker.CfnEndpoint(this, 'DeciderEndpoint', {
      endpointName: DECIDER_ENDPOINT_NAME,
      endpointConfigName: endpointConfig.attrEndpointConfigName,
    });
    this.endpointArn = endpoint.ref; // Ref of AWS::SageMaker::Endpoint is its ARN

    // ---- Auto-scaling on invocations per instance (minimum stays warm)
    const target = new appscaling.ScalableTarget(this, 'DeciderScalableTarget', {
      serviceNamespace: appscaling.ServiceNamespace.SAGEMAKER,
      resourceId: `endpoint/${DECIDER_ENDPOINT_NAME}/variant/${VARIANT_NAME}`,
      scalableDimension: 'sagemaker:variant:DesiredInstanceCount',
      minCapacity: props.minInstances,
      maxCapacity: props.maxInstances,
    });
    target.node.addDependency(endpoint);
    target.scaleToTrackMetric('InvocationsPerInstance', {
      predefinedMetric: appscaling.PredefinedMetric.SAGEMAKER_VARIANT_INVOCATIONS_PER_INSTANCE,
      targetValue: props.invocationsPerInstanceTarget,
      scaleOutCooldown: cdk.Duration.minutes(2),
      scaleInCooldown: cdk.Duration.minutes(10),
    });

    // ---- In-Region latency measurement 
    const measureSource = new s3assets.Asset(this, 'DeciderMeasureSource', { path: '../scripts', exclude: ['__pycache__'] });
    const measureProject = new codebuild.Project(this, 'DeciderMeasureProject', {
      projectName: 'VoiceShop-DeciderMeasure',
      description: 'Measure decider latency and InvokeEndpoint overhead from inside the Region.',
      source: codebuild.Source.s3({ bucket: measureSource.bucket, path: measureSource.s3ObjectKey }),
      environment: { buildImage: codebuild.LinuxBuildImage.STANDARD_7_0, computeType: codebuild.ComputeType.SMALL },
      environmentVariables: {
        ENDPOINT_NAME: { value: DECIDER_ENDPOINT_NAME },
        WARM_CALLS: { value: '50' },
      },
      buildSpec: codebuild.BuildSpec.fromObject({
        version: '0.2',
        phases: {
          install: { 'runtime-versions': { python: '3.12' }, commands: ['pip install --quiet boto3'] },
          build: {
            commands: ['python3 -u measure-latency.py --endpoint-name "${ENDPOINT_NAME}" --region "${AWS_REGION}" --warm-calls "${WARM_CALLS}"'],
          },
        },
      }),
      timeout: cdk.Duration.minutes(30),
    });
    measureProject.addToRolePolicy(new iam.PolicyStatement({
      actions: ['sagemaker:InvokeEndpoint'],
      resources: [this.endpointArn],
    }));

    NagSuppressions.addResourceSuppressions(endpointConfig, [
      {
        id: 'AwsSolutions-SM2',
        reason: 'g6 instances use local NVMe instance storage, for which SageMaker does not accept a KmsKeyId; the volume holds only public model weights.',
      },
    ]);
    NagSuppressions.addResourceSuppressions(
      [executionRole, measureProject],
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'ecr:GetAuthorizationToken has no resource-level scoping; cloudwatch:PutMetricData is namespace-conditioned; S3 is scoped to the model/ prefix; CodeBuild-generated log/report statements use the wildcards CodeBuild requires.',
        },
        {
          id: 'AwsSolutions-CB4',
          reason: 'Measurement-only build with no artifacts; CodeBuild uses the AWS-managed default encryption.',
        },
      ],
      true,
    );

    new cdk.CfnOutput(this, 'EndpointNameOutput', { value: DECIDER_ENDPOINT_NAME, exportName: 'VoiceShop-DeciderEndpointStack-EndpointName' });
    new cdk.CfnOutput(this, 'EndpointArnOutput', { value: this.endpointArn, exportName: 'VoiceShop-DeciderEndpointStack-EndpointArn' });
  }
}
