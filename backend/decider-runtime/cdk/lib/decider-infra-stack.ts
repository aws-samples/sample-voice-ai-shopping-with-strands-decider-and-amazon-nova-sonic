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
// Model weights, copied once into the model bucket at deploy time; the endpoint never reaches
// Hugging Face (network isolation). Both are pinned to a commit, so a fresh deploy always serves the
// same files. The decider comes from the amazon organization, Strands Decider's mirror with the same
// files as StrandsAgents (all 29 file hashes compared, 2026-10-23). The Qwen base model has no mirror.
const DECIDER_CHECKPOINT_REPO = 'amazon/strands-decider-2B-qwen3.5-v1-2610';
const DECIDER_CHECKPOINT_REVISION = '974685f92d5930b2d37e9cb5eacec7a8f882d9fa';
const BASE_MODEL_REPO = 'Qwen/Qwen3.5-2B-Base';
const BASE_MODEL_REVISION = 'b1485b2fa6dfa1287294f269f5fb618e03d52d7c';

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
      description: 'Downloads the Strands Decider checkpoint (amazon organization) and its Qwen3.5-2B base model from Hugging Face, at pinned commits, into the model bucket.',
      environment: { buildImage: codebuild.LinuxBuildImage.STANDARD_7_0, computeType: codebuild.ComputeType.MEDIUM },
      environmentVariables: {
        MODEL_BUCKET: { value: this.modelBucket.bucketName },
        CHECKPOINT_REPO: { value: DECIDER_CHECKPOINT_REPO },
        CHECKPOINT_REVISION: { value: DECIDER_CHECKPOINT_REVISION },
        BASE_MODEL_REPO: { value: BASE_MODEL_REPO },
        BASE_MODEL_REVISION: { value: BASE_MODEL_REVISION },
      },
      buildSpec: codebuild.BuildSpec.fromObject({
        version: '0.2',
        phases: {
          install: { 'runtime-versions': { python: '3.12' }, commands: ['pip install --quiet huggingface_hub==0.36.0'] },
          build: {
            commands: [
              // SageMaker mounts s3://<bucket>/model/ at /opt/ml/model (container/serve.py).
              'if aws s3api head-object --bucket "$MODEL_BUCKET" --key model/checkpoint/strands_decider_config.json >/dev/null 2>&1; then echo "checkpoint present"; else '
                + 'huggingface-cli download "$CHECKPOINT_REPO" --revision "$CHECKPOINT_REVISION" --local-dir /tmp/checkpoint '
                + '&& aws s3 sync /tmp/checkpoint "s3://$MODEL_BUCKET/model/checkpoint/" --no-progress --exclude ".cache/*"; fi',
              // aws s3 sync follows snapshots/ -> blobs/ symlinks, so the blob store is skipped. A download by
              // commit writes no refs/main, which the container's offline load resolves, so it is written here.
              'if aws s3api head-object --bucket "$MODEL_BUCKET" --key model/hf-cache/models--Qwen--Qwen3.5-2B-Base/refs/main >/dev/null 2>&1; then echo "base model present"; else '
                + 'huggingface-cli download "$BASE_MODEL_REPO" --revision "$BASE_MODEL_REVISION" --cache-dir /tmp/hf-cache '
                + '&& mkdir -p /tmp/hf-cache/models--Qwen--Qwen3.5-2B-Base/refs '
                + '&& printf "%s" "$BASE_MODEL_REVISION" > /tmp/hf-cache/models--Qwen--Qwen3.5-2B-Base/refs/main '
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
