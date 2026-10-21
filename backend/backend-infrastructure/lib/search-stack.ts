import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as aoss from 'aws-cdk-lib/aws-opensearchserverless';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { NagSuppressions } from 'cdk-nag';

export const CATALOG_COLLECTION_NAME = 'voiceshop-catalog';

export interface SearchStackProps extends cdk.StackProps {
  readonly vpc: ec2.IVpc;
  readonly subnets: ec2.ISubnet[];
}

/**
 * OpenSearch Serverless vector collection for the catalog (ADR-8). Private
 * (ADR-32): the network policy allows only this stack's VPC endpoint, and every
 * request must also be SigV4-signed by a role that a data access policy names.
 *
 * The data access policy is NOT here: it must name the reader/writer roles,
 * which live in VoiceShop-LambdaStack (search-catalog) and the catalog-data app. Each of those owns a data access policy for its own role, which
 * avoids a SearchStack <-> LambdaStack dependency cycle.
 */
export class SearchStack extends cdk.Stack {
  public readonly collection: aoss.CfnCollection;
  public readonly vpcEndpoint: aoss.CfnVpcEndpoint;

  constructor(scope: Construct, id: string, props: SearchStackProps) {
    super(scope, id, props);

    // The collection's private endpoint (an OpenSearch Serverless VPC endpoint,
    // not an EC2 interface endpoint). It also gives the collection hostname a
    // private DNS answer inside the VPC, so callers keep the same URL.
    const endpointSecurityGroup = new ec2.SecurityGroup(this, 'AossEndpointSecurityGroup', {
      vpc: props.vpc,
      description: 'OpenSearch Serverless VPC endpoint: HTTPS from inside the VPC only.',
      allowAllOutbound: false,
    });
    endpointSecurityGroup.addIngressRule(ec2.Peer.ipv4(props.vpc.vpcCidrBlock), ec2.Port.tcp(443),
      'HTTPS from search-catalog and catalog-index-writer');
    NagSuppressions.addResourceSuppressions(endpointSecurityGroup, [{
      id: 'CdkNagValidationFailure',
      reason: 'AwsSolutions-EC23 cannot evaluate the VPC CIDR token; the only ingress is HTTPS from this VPC\'s CIDR.',
    }]);
    this.vpcEndpoint = new aoss.CfnVpcEndpoint(this, 'AossVpcEndpoint', {
      name: 'voiceshop-aoss-vpce',
      vpcId: props.vpc.vpcId,
      subnetIds: props.subnets.map((s) => s.subnetId),
      securityGroupIds: [endpointSecurityGroup.securityGroupId],
    });

    // =====================================================================
    // Policies: encryption must exist before the collection is created.
    // =====================================================================
    const encryptionPolicy = new aoss.CfnSecurityPolicy(this, 'EncryptionPolicy', {
      name: 'voiceshop-catalog-enc',
      type: 'encryption',
      policy: JSON.stringify({
        Rules: [{ ResourceType: 'collection', Resource: [`collection/${CATALOG_COLLECTION_NAME}`] }],
        AWSOwnedKey: true,
      }),
    });

    const networkPolicy = new aoss.CfnSecurityPolicy(this, 'NetworkPolicy', {
      name: 'voiceshop-catalog-net',
      type: 'network',
      policy: JSON.stringify([
        {
          Rules: [{ ResourceType: 'collection', Resource: [`collection/${CATALOG_COLLECTION_NAME}`] }],
          AllowFromPublic: false,
          SourceVPCEs: [this.vpcEndpoint.attrId],
        },
      ]),
    });

    this.collection = new aoss.CfnCollection(this, 'CatalogCollection', {
      name: CATALOG_COLLECTION_NAME,
      type: 'VECTORSEARCH',
      standbyReplicas: 'DISABLED',
      description: 'VoiceShop curated catalog: Titan V2 embeddings + metadata filters (ADR-8).',
    });
    this.collection.addResourceDependency(encryptionPolicy);
    this.collection.addResourceDependency(networkPolicy);

    new cdk.CfnOutput(this, 'CollectionEndpoint', {
      value: this.collection.attrCollectionEndpoint,
      exportName: 'VoiceShop-SearchStack-CollectionEndpoint',
    });
    new cdk.CfnOutput(this, 'CollectionArn', {
      value: this.collection.attrArn,
      exportName: 'VoiceShop-SearchStack-CollectionArn',
    });
    new cdk.CfnOutput(this, 'CollectionName', {
      value: CATALOG_COLLECTION_NAME,
      exportName: 'VoiceShop-SearchStack-CollectionName',
    });
  }
}
