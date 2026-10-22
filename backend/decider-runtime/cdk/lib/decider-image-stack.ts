import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ecr from 'aws-cdk-lib/aws-ecr';

/**
 * The decider's ECR repository, in a stack of its own so cleanup-all.sh
 * --keep-decider-image can keep it (and the 13.5 GB image in it) between
 * deployments: the next deploy finds the image by its content tag and skips the
 * ~16 minute build. A normal cleanup deletes it with everything else.
 */
export class DeciderImageStack extends cdk.Stack {
  public readonly repository: ecr.Repository;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    this.repository = new ecr.Repository(this, 'DeciderRepository', {
      repositoryName: 'voiceshop-decider',
      imageScanOnPush: true,
      emptyOnDelete: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    new cdk.CfnOutput(this, 'RepositoryUriOutput', {
      value: this.repository.repositoryUri,
      exportName: 'VoiceShop-DeciderImageStack-RepositoryUri',
    });
  }
}
