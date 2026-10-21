import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as codebuild from 'aws-cdk-lib/aws-codebuild';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3assets from 'aws-cdk-lib/aws-s3-assets';
import { NagSuppressions } from 'cdk-nag';

const PRODUCTS_TABLE_NAME = 'VoiceShop-Products';
const TITAN_EMBED_MODEL_ID = 'amazon.titan-embed-text-v2:0';

export interface CatalogDataStackProps extends cdk.StackProps {
  /** VoiceShop-CatalogIndexWriter, in the search collection's VPC (ADR-32). */
  readonly indexWriterFunctionArn: string;
  readonly imagesBucketName: string;
}

/**
 * Catalog loading (ADR-27): one CodeBuild project, VoiceShop-CatalogCuration,
 * loads the synthetic catalog that ships in this package (catalog/*.zip, pinned
 * by catalog/catalog.lock.json) into the images bucket and the Products table,
 * and embeds every product. The search collection is reachable only from its
 * VPC (ADR-32), so the job writes the finished index documents to the images
 * bucket (catalog-index/) and invokes VoiceShop-CatalogIndexWriter, which runs in
 * the VPC and rebuilds the index. The job itself needs no VPC.
 * The package itself is the build source (a CDK asset), so the catalog never
 * leaves AWS-managed storage. deploy-all.sh starts the build; it does not run on
 * stack deploy.
 */
export class CatalogDataStack extends cdk.Stack {
  public readonly curationProject: codebuild.Project;

  constructor(scope: Construct, id: string, props: CatalogDataStackProps) {
    super(scope, id, props);

    const source = new s3assets.Asset(this, 'CatalogPackageAsset', {
      path: '..',
      exclude: ['cdk', 'tests', 'generate', '**/__pycache__', '.pytest_cache', 'requirements-dev.txt', 'scripts'],
    });

    this.curationProject = new codebuild.Project(this, 'CatalogCurationProject', {
      projectName: 'VoiceShop-CatalogCuration',
      description: 'Load the synthetic catalog: images to S3, products to DynamoDB, Titan V2 embeddings to the index writer.',
      source: codebuild.Source.s3({ bucket: source.bucket, path: source.s3ObjectKey }),
      environment: { buildImage: codebuild.LinuxBuildImage.STANDARD_7_0, computeType: codebuild.ComputeType.MEDIUM },
      environmentVariables: {
        IMAGES_BUCKET: { value: props.imagesBucketName },
        PRODUCTS_TABLE: { value: PRODUCTS_TABLE_NAME },
        INDEX_WRITER_FUNCTION: { value: props.indexWriterFunctionArn },
      },
      buildSpec: codebuild.BuildSpec.fromSourceFilename('curation-buildspec.yml'),
      timeout: cdk.Duration.minutes(60),
    });

    // Least privilege: write and prune product images, write the Products table,
    // embed with Titan V2, hand the index documents to the index writer.
    const images = `arn:${cdk.Aws.PARTITION}:s3:::${props.imagesBucketName}`;
    this.curationProject.addToRolePolicy(new iam.PolicyStatement({
      actions: ['s3:PutObject', 's3:DeleteObject'],
      resources: [`${images}/products/*`, `${images}/catalog-index/*`],
    }));
    this.curationProject.addToRolePolicy(new iam.PolicyStatement({
      actions: ['s3:ListBucket'],
      resources: [images],
      conditions: { StringLike: { 's3:prefix': ['products/*'] } },
    }));
    this.curationProject.addToRolePolicy(new iam.PolicyStatement({
      // Scan + BatchWriteItem deletes: products no longer in the catalog are removed.
      actions: ['dynamodb:BatchWriteItem', 'dynamodb:PutItem', 'dynamodb:Scan'],
      resources: [`arn:${cdk.Aws.PARTITION}:dynamodb:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:table/${PRODUCTS_TABLE_NAME}`],
    }));
    this.curationProject.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel'],
      resources: [`arn:${cdk.Aws.PARTITION}:bedrock:${cdk.Aws.REGION}::foundation-model/${TITAN_EMBED_MODEL_ID}`],
    }));
    this.curationProject.addToRolePolicy(new iam.PolicyStatement({
      actions: ['lambda:InvokeFunction'],
      resources: [props.indexWriterFunctionArn],
    }));

    NagSuppressions.addResourceSuppressions(
      this.curationProject,
      [
        {
          id: 'AwsSolutions-CB4',
          reason: 'The job writes to S3 and DynamoDB directly and produces no artifacts; CodeBuild uses the AWS-managed default encryption.',
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Object actions are limited to the images bucket products/* and catalog-index/* prefixes; the CodeBuild-generated log and report-group statements use the wildcards CodeBuild requires.',
        },
      ],
      true,
    );

    new cdk.CfnOutput(this, 'CatalogCurationProjectName', { value: this.curationProject.projectName });
  }
}
