import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3assets from 'aws-cdk-lib/aws-s3-assets';
import * as codebuild from 'aws-cdk-lib/aws-codebuild';
import * as iam from 'aws-cdk-lib/aws-iam';
import { NagSuppressions } from 'cdk-nag';

/** Prefix in the model bucket that SageMaker mounts at /opt/ml/model. */
export const MODEL_DATA_PREFIX = 'model/';
const DECIDER_CHECKPOINT_REPO = 'StrandsAgents/strands-decider-2B-qwen3.5-v1-2610';
const BASE_MODEL_REPO = 'Qwen/Qwen3.5-2B-Base';

export interface DeciderInfraStackProps extends cdk.StackProps {
  /** The decider ECR repository (VoiceShop-DeciderImageStack). */
  readonly repository: ecr.IRepository;
}

/**
 * Everything the decider endpoint needs before it can be created (ADR-22): the
 * x86_64 CodeBuild project that builds and pushes the image (no local Docker
 * build), the model bucket that holds the strands-decider checkpoint and the
 * Qwen3.5-2B base model under model/, and the CodeBuild project that downloads
 * them from Hugging Face into that bucket (in-Region, ~1-2 minutes).
 */
export class DeciderInfraStack extends cdk.Stack {
  public readonly repository: ecr.IRepository;
  public readonly buildProject: codebuild.Project;
  public readonly modelUploadProject: codebuild.Project;
  public readonly modelBucket: s3.Bucket;

  constructor(scope: Construct, id: string, props: DeciderInfraStackProps) {
    super(scope, id, props);
    this.repository = props.repository;

    // model/checkpoint/ (strands-decider LoRA adapter + pointer head, ~235 MB) and
    // model/hf-cache/ (Qwen3.5-2B base model as a Hugging Face cache, ~4.6 GB),
    // written once by the VoiceShop-DeciderModelUpload project.
    this.modelBucket = new s3.Bucket(this, 'DeciderModelBucket', {
      bucketName: `voiceshop-decider-model-${this.account}-${this.region}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
      autoDeleteObjects: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const sourceAsset = new s3assets.Asset(this, 'DeciderSourceAsset', {
      path: '../container',
      exclude: ['__pycache__'],
    });

    this.buildProject = new codebuild.Project(this, 'DeciderBuildProject', {
      projectName: 'VoiceShop-DeciderBuild',
      source: codebuild.Source.s3({ bucket: sourceAsset.bucket, path: sourceAsset.s3ObjectKey }),
      environment: {
        buildImage: codebuild.LinuxBuildImage.STANDARD_7_0, // x86_64
        computeType: codebuild.ComputeType.LARGE,
        privileged: true, // docker build/push
      },
      environmentVariables: {
        ECR_REPOSITORY_URI: { value: this.repository.repositoryUri },
        AWS_REGION: { value: this.region },
      },
      buildSpec: codebuild.BuildSpec.fromSourceFilename('buildspec.yml'),
      timeout: cdk.Duration.minutes(90),
    });
    this.repository.grantPullPush(this.buildProject);
    this.buildProject.addToRolePolicy(new iam.PolicyStatement({
      actions: ['ecr:DescribeImages'],
      resources: [this.repository.repositoryArn],
    }));
    this.buildProject.addToRolePolicy(new iam.PolicyStatement({
      actions: ['ecr:GetAuthorizationToken'],
      resources: ['*'],
    }));

    // ---- One-time model upload, in-Region (skipped when the files are already there)
    this.modelUploadProject = new codebuild.Project(this, 'DeciderModelUploadProject', {
      projectName: 'VoiceShop-DeciderModelUpload',
      description: 'Downloads the Strands Decider checkpoint and its Qwen3.5-2B base model from Hugging Face into the model bucket.',
      environment: { buildImage: codebuild.LinuxBuildImage.STANDARD_7_0, computeType: codebuild.ComputeType.MEDIUM },
      environmentVariables: {
        MODEL_BUCKET: { value: this.modelBucket.bucketName },
        CHECKPOINT_REPO: { value: DECIDER_CHECKPOINT_REPO },
        BASE_MODEL_REPO: { value: BASE_MODEL_REPO },
      },
      buildSpec: codebuild.BuildSpec.fromObject({
        version: '0.2',
        phases: {
          install: { 'runtime-versions': { python: '3.12' }, commands: ['pip install --quiet huggingface_hub==0.36.0'] },
          build: {
            commands: [
              // SageMaker mounts s3://<bucket>/model/ at /opt/ml/model (container/serve.py).
              'if aws s3api head-object --bucket "$MODEL_BUCKET" --key model/checkpoint/strands_decider_config.json >/dev/null 2>&1; then echo "checkpoint present"; else '
                + 'huggingface-cli download "$CHECKPOINT_REPO" --local-dir /tmp/checkpoint '
                + '&& aws s3 sync /tmp/checkpoint "s3://$MODEL_BUCKET/model/checkpoint/" --no-progress --exclude ".cache/*"; fi',
              // aws s3 sync follows snapshots/ -> blobs/ symlinks, so the blob store is skipped.
              'if aws s3api head-object --bucket "$MODEL_BUCKET" --key model/hf-cache/models--Qwen--Qwen3.5-2B-Base/refs/main >/dev/null 2>&1; then echo "base model present"; else '
                + 'huggingface-cli download "$BASE_MODEL_REPO" --cache-dir /tmp/hf-cache '
                + '&& aws s3 sync /tmp/hf-cache "s3://$MODEL_BUCKET/model/hf-cache/" --no-progress --exclude "*/blobs/*" --exclude ".locks/*" --exclude "*/.locks/*"; fi',
            ],
          },
        },
      }),
      timeout: cdk.Duration.minutes(30),
    });
    this.modelBucket.grantReadWrite(this.modelUploadProject);

    NagSuppressions.addResourceSuppressions(this.modelBucket, [
      {
        id: 'AwsSolutions-S1',
        reason: 'Holds public model weights uploaded once by the deploy script; read only by the SageMaker execution role. No shopper data.',
      },
    ]);
    NagSuppressions.addResourceSuppressions(
      [this.buildProject, this.modelUploadProject],
      [
        {
          id: 'AwsSolutions-CB4',
          reason: 'The image build produces no CodeBuild artifacts (it pushes to ECR); CodeBuild uses the AWS-managed default encryption.',
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'ecr:GetAuthorizationToken has no resource-level permissions and must use "*"; push/pull is scoped to this one repository; the model upload writes only the model bucket. CodeBuild-generated log and report-group statements use the wildcards CodeBuild requires.',
        },
      ],
      true,
    );

    new cdk.CfnOutput(this, 'ModelBucketOutput', {
      value: this.modelBucket.bucketName,
      exportName: 'VoiceShop-DeciderInfraStack-ModelBucket',
    });
  }
}
