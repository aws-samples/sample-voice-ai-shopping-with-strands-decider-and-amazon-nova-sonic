import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { NagSuppressions } from 'cdk-nag';

/**
 * Product images bucket (products/<asin>/image.webp, one 384 px image per product;
 * catalog-index/ holds the index documents during a catalog load) and its server
 * access log bucket. Private: images reach the browser only as 1-hour
 * presigned GET URLs minted by the tool Lambdas (ADR-13).
 */
export class StorageStack extends cdk.Stack {
  public readonly imagesBucket: s3.Bucket;
  public readonly accessLogBucket: s3.Bucket;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    this.accessLogBucket = new s3.Bucket(this, 'AccessLogBucket', {
      bucketName: `voiceshop-access-logs-${this.account}-${this.region}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
      autoDeleteObjects: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.imagesBucket = new s3.Bucket(this, 'ImagesBucket', {
      // Predictable name: the catalog curation app (a separate CDK app)
      // writes into it.
      bucketName: `voiceshop-images-${this.account}-${this.region}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      serverAccessLogsBucket: this.accessLogBucket,
      serverAccessLogsPrefix: 'images/',
      autoDeleteObjects: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    NagSuppressions.addResourceSuppressions(this.accessLogBucket, [
      {
        id: 'AwsSolutions-S1',
        reason: 'This is the server access log destination bucket itself; logging it to another bucket would only move the same finding.',
      },
    ]);

    new cdk.CfnOutput(this, 'ImagesBucketName', {
      value: this.imagesBucket.bucketName,
      exportName: 'VoiceShop-StorageStack-ImagesBucketName',
    });
  }
}
