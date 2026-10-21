import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as logs from 'aws-cdk-lib/aws-logs';
import { NagSuppressions } from 'cdk-nag';

export interface NetworkStackProps extends cdk.StackProps {
  /**
   * The one Availability Zone of this sample (ADR-32). The OpenSearch Serverless
   * VPC endpoint service supports only some zones (in us-east-1: 1a, 1b and 1d).
   */
  readonly availabilityZone: string;
}

/**
 * The private network for catalog search (ADR-32): the search collection accepts
 * requests only through its VPC endpoint, so the two functions that talk to it
 * (search-catalog and catalog-index-writer) run here.
 *
 * One Availability Zone, isolated subnet, no NAT gateway and no internet route.
 * A production deployment spans at least two zones (one subnet per zone for the
 * functions and the endpoints), so one zone's outage does not stop search.
 */
export class NetworkStack extends cdk.Stack {
  public readonly vpc: ec2.Vpc;
  public readonly subnets: ec2.ISubnet[];

  constructor(scope: Construct, id: string, props: NetworkStackProps) {
    super(scope, id, props);

    this.vpc = new ec2.Vpc(this, 'Vpc', {
      vpcName: 'VoiceShop-Vpc',
      availabilityZones: [props.availabilityZone],
      natGateways: 0,
      subnetConfiguration: [{ name: 'Private', subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 }],
    });
    this.subnets = this.vpc.isolatedSubnets;

    // Free gateway endpoint: the index writer reads the documents file the
    // catalog loader leaves in the images bucket.
    this.vpc.addGatewayEndpoint('S3Endpoint', {
      service: ec2.GatewayVpcEndpointAwsService.S3,
      subnets: [{ subnetType: ec2.SubnetType.PRIVATE_ISOLATED }],
    });

    // search-catalog embeds the shopper's query with Titan Text Embeddings V2.
    const bedrockEndpoint = this.vpc.addInterfaceEndpoint('BedrockRuntimeEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      privateDnsEnabled: true,
    });
    NagSuppressions.addResourceSuppressions(bedrockEndpoint, [{
      id: 'CdkNagValidationFailure',
      reason: 'AwsSolutions-EC23 cannot evaluate the VPC CIDR token; the endpoint security group allows HTTPS from this VPC\'s CIDR only.',
    }], true);

    // The OpenSearch Serverless endpoint is not here: it is created through the
    // OpenSearch Serverless API (AWS::OpenSearchServerless::VpcEndpoint), next to
    // the network policy that names it, in VoiceShop-SearchStack.

    const flowLogGroup = new logs.LogGroup(this, 'FlowLogGroup', {
      logGroupName: '/voiceshop/network/flow-logs',
      retention: logs.RetentionDays.TWO_WEEKS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    this.vpc.addFlowLog('FlowLog', {
      destination: ec2.FlowLogDestination.toCloudWatchLogs(flowLogGroup),
      trafficType: ec2.FlowLogTrafficType.ALL,
    });
    NagSuppressions.addResourceSuppressions(this.vpc, [{
      id: 'AwsSolutions-VPC7',
      reason: 'Flow logs are enabled above via addFlowLog; this suppression covers the nag rule firing before the aspect sees the added resource.',
    }], true);

    new cdk.CfnOutput(this, 'VpcIdOutput', { value: this.vpc.vpcId, exportName: 'VoiceShop-NetworkStack-VpcId' });
  }
}
