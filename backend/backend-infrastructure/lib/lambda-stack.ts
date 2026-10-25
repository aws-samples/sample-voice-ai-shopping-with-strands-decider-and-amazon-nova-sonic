import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as nodejs from 'aws-cdk-lib/aws-lambda-nodejs';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as aoss from 'aws-cdk-lib/aws-opensearchserverless';
import { NagSuppressions } from 'cdk-nag';
import { CATALOG_COLLECTION_NAME } from './search-stack';

export interface LambdaStackProps extends cdk.StackProps {
  readonly productsTable: dynamodb.ITable;
  readonly cartTable: dynamodb.ITable;
  readonly ordersTable: dynamodb.ITable;
  readonly imagesBucket: s3.IBucket;
  readonly collection: aoss.CfnCollection;
  /** The private network of the search collection (ADR-32). */
  readonly vpc: ec2.IVpc;
  readonly subnets: ec2.ISubnet[];
}

const CATALOG_INDEX_NAME = 'products';
const TITAN_EMBED_MODEL_ID = 'amazon.titan-embed-text-v2:0';

/**
 * The five tool Lambdas behind AgentCore Gateway (ADR-12), one least-privilege
 * role each, and the catalog index writer. The search collection is reachable
 * only through its VPC endpoint (ADR-32), so search-catalog and
 * catalog-index-writer run in the VPC; the other three call DynamoDB and S3 over
 * the public AWS endpoints, signed with their roles.
 */
export class LambdaStack extends cdk.Stack {
  public readonly searchCatalog: nodejs.NodejsFunction;
  public readonly getProductDetail: nodejs.NodejsFunction;
  public readonly addToCart: nodejs.NodejsFunction;
  public readonly placeOrder: nodejs.NodejsFunction;
  public readonly removeFromCart: nodejs.NodejsFunction;
  public readonly catalogIndexWriter: nodejs.NodejsFunction;

  constructor(scope: Construct, id: string, props: LambdaStackProps) {
    super(scope, id, props);

    const makeFunction = (name: string, description: string, environment: Record<string, string>, memorySize = 512,
      network?: { securityGroup: ec2.ISecurityGroup }, timeout = cdk.Duration.seconds(10)) => {
      const logGroup = new logs.LogGroup(this, `${name}LogGroup`, {
        logGroupName: `/aws/lambda/VoiceShop-${name}`,
        retention: logs.RetentionDays.TWO_WEEKS,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      });
      return new nodejs.NodejsFunction(this, name, {
        functionName: `VoiceShop-${name}`,
        description,
        entry: path.join(__dirname, `../lambda/${kebab(name)}/index.js`),
        handler: 'handler',
        runtime: lambda.Runtime.NODEJS_22_X,
        architecture: lambda.Architecture.ARM_64,
        memorySize,
        timeout,
        logGroup,
        ...(network ? {
          vpc: props.vpc,
          vpcSubnets: { subnets: props.subnets },
          securityGroups: [network.securityGroup],
        } : {}),
        environment,
        bundling: {
          // Bundle the SDK too, rather than relying on the runtime's copy:
          // the SigV4 signer the image presigning uses is not guaranteed there.
          externalModules: [],
          minify: true,
          target: 'node22',
        },
      });
    };

    const imageObjectsArn = props.imagesBucket.arnForObjects('products/*');

    // HTTPS to the VPC endpoints only (the subnet has no internet route).
    const searchSecurityGroup = new ec2.SecurityGroup(this, 'SearchCatalogSecurityGroup', {
      vpc: props.vpc,
      description: 'search-catalog: HTTPS to the OpenSearch Serverless and Bedrock runtime VPC endpoints.',
      allowAllOutbound: false,
    });
    searchSecurityGroup.addEgressRule(ec2.Peer.ipv4(props.vpc.vpcCidrBlock), ec2.Port.tcp(443), 'VPC endpoints');

    // ---- search_catalog -------------------------------------------------
    this.searchCatalog = makeFunction('SearchCatalog', 'search_catalog tool: Titan V2 query embedding + filtered k-NN on the catalog collection.', {
      COLLECTION_ENDPOINT: props.collection.attrCollectionEndpoint,
      INDEX_NAME: CATALOG_INDEX_NAME,
      IMAGES_BUCKET_NAME: props.imagesBucket.bucketName,
    // 1,024 MB for the CPU share: search_catalog is on the per-sentence path and
    // signs up to 200 thumbnail URLs per call (measured).
    }, 1024, { securityGroup: searchSecurityGroup });
    this.searchCatalog.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel'],
      resources: [`arn:${cdk.Aws.PARTITION}:bedrock:${cdk.Aws.REGION}::foundation-model/${TITAN_EMBED_MODEL_ID}`],
    }));
    this.searchCatalog.addToRolePolicy(new iam.PolicyStatement({
      actions: ['aoss:APIAccessAll'],
      resources: [props.collection.attrArn],
    }));
    this.searchCatalog.addToRolePolicy(new iam.PolicyStatement({ actions: ['s3:GetObject'], resources: [imageObjectsArn] }));

    // AOSS data access policy for this one role: read-only on the products index.
    // Owned here (not in SearchStack) because it must name this role.
    new aoss.CfnAccessPolicy(this, 'SearchCatalogDataAccessPolicy', {
      name: 'voiceshop-search-read',
      type: 'data',
      policy: JSON.stringify([
          {
            Rules: [
              {
                ResourceType: 'index',
                Resource: [`index/${CATALOG_COLLECTION_NAME}/${CATALOG_INDEX_NAME}`],
                Permission: ['aoss:ReadDocument', 'aoss:DescribeIndex'],
              },
            ],
            Principal: [this.searchCatalog.role!.roleArn],
          },
        ]),
    });

    // ---- catalog-index-writer ---------------------------------------------
    // Invoked by the catalog loader (VoiceShop-CatalogCuration, outside the VPC)
    // with the key of the documents file it wrote; rebuilds the products index.
    const writerSecurityGroup = new ec2.SecurityGroup(this, 'CatalogIndexWriterSecurityGroup', {
      vpc: props.vpc,
      description: 'catalog-index-writer: HTTPS to the OpenSearch Serverless VPC endpoint and to S3 through its gateway endpoint.',
      // S3 is reached through the gateway endpoint, whose addresses are an AWS
      // prefix list; the subnet has no internet route, so all outbound is safe.
      allowAllOutbound: true,
    });
    this.catalogIndexWriter = makeFunction('CatalogIndexWriter', 'Rebuilds the catalog search index from the documents file the catalog loader writes.', {
      COLLECTION_ENDPOINT: props.collection.attrCollectionEndpoint,
      INDEX_NAME: CATALOG_INDEX_NAME,
      IMAGES_BUCKET_NAME: props.imagesBucket.bucketName,
    }, 1024, { securityGroup: writerSecurityGroup }, cdk.Duration.minutes(10));
    this.catalogIndexWriter.addToRolePolicy(new iam.PolicyStatement({
      actions: ['aoss:APIAccessAll'],
      resources: [props.collection.attrArn],
    }));
    this.catalogIndexWriter.addToRolePolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject'],
      resources: [props.imagesBucket.arnForObjects('catalog-index/*')],
    }));
    new aoss.CfnAccessPolicy(this, 'CatalogIndexWriterDataAccessPolicy', {
      name: 'voiceshop-index-write',
      type: 'data',
      policy: JSON.stringify([
        {
          Rules: [
            {
              ResourceType: 'index',
              Resource: [`index/${CATALOG_COLLECTION_NAME}/${CATALOG_INDEX_NAME}`],
              Permission: ['aoss:CreateIndex', 'aoss:DeleteIndex', 'aoss:UpdateIndex', 'aoss:DescribeIndex',
                'aoss:ReadDocument', 'aoss:WriteDocument'],
            },
          ],
          Principal: [this.catalogIndexWriter.role!.roleArn],
        },
      ]),
    });

    // ---- get_product_detail ---------------------------------------------
    this.getProductDetail = makeFunction('GetProductDetail', 'get_product_detail tool: full product record + presigned image URLs.', {
      PRODUCTS_TABLE_NAME: props.productsTable.tableName,
      IMAGES_BUCKET_NAME: props.imagesBucket.bucketName,
    });
    this.getProductDetail.addToRolePolicy(new iam.PolicyStatement({ actions: ['dynamodb:GetItem'], resources: [props.productsTable.tableArn] }));
    this.getProductDetail.addToRolePolicy(new iam.PolicyStatement({ actions: ['s3:GetObject'], resources: [imageObjectsArn] }));

    // ---- add_to_cart ------------------------------------------------------
    this.addToCart = makeFunction('AddToCart', 'add_to_cart tool: idempotent cart line upsert in VoiceShop-Cart.', {
      PRODUCTS_TABLE_NAME: props.productsTable.tableName,
      CART_TABLE_NAME: props.cartTable.tableName,
    });
    this.addToCart.addToRolePolicy(new iam.PolicyStatement({ actions: ['dynamodb:GetItem'], resources: [props.productsTable.tableArn] }));
    // One conditional UpdateItem per call (compare-and-set on the line's quantity); Query reads the cart back.
    this.addToCart.addToRolePolicy(new iam.PolicyStatement({ actions: ['dynamodb:UpdateItem', 'dynamodb:Query'], resources: [props.cartTable.tableArn] }));

    // ---- remove_from_cart -------------------------------------------------
    this.removeFromCart = makeFunction('RemoveFromCart', 'remove_from_cart tool: deletes one cart line in VoiceShop-Cart.', {
      CART_TABLE_NAME: props.cartTable.tableName,
    });
    this.removeFromCart.addToRolePolicy(new iam.PolicyStatement({ actions: ['dynamodb:DeleteItem', 'dynamodb:UpdateItem', 'dynamodb:Query'], resources: [props.cartTable.tableArn] }));

    // ---- place_order ------------------------------------------------------
    this.placeOrder = makeFunction('PlaceOrder', 'place_order tool: cart -> one idempotent order in VoiceShop-Orders, then empties the cart.', {
      CART_TABLE_NAME: props.cartTable.tableName,
      ORDERS_TABLE_NAME: props.ordersTable.tableName,
    });
    this.placeOrder.addToRolePolicy(new iam.PolicyStatement({ actions: ['dynamodb:Query', 'dynamodb:DeleteItem'], resources: [props.cartTable.tableArn] }));
    this.placeOrder.addToRolePolicy(new iam.PolicyStatement({ actions: ['dynamodb:GetItem', 'dynamodb:PutItem'], resources: [props.ordersTable.tableArn] }));

    for (const fn of [this.searchCatalog, this.getProductDetail, this.addToCart, this.removeFromCart, this.placeOrder, this.catalogIndexWriter]) {
      NagSuppressions.addResourceSuppressions(
        fn,
        [
          {
            id: 'AwsSolutions-IAM4',
            reason: 'AWSLambdaBasicExecutionRole (and, for the two VPC functions, AWSLambdaVPCAccessExecutionRole) are the AWS-managed policies CDK attaches for CloudWatch Logs and VPC network interfaces.',
          },
          {
            id: 'AwsSolutions-L1',
            reason: 'Node.js 22.x is pinned on purpose; move to a newer runtime deliberately, with the tests re-run.',
          },
          {
            id: 'AwsSolutions-IAM5',
            reason: 's3:GetObject is scoped to one prefix of the images bucket: products/* (presigned image URLs) or catalog-index/* (the index writer).',
          },
        ],
        true,
      );
    }

    const outputs: Record<string, nodejs.NodejsFunction> = {
      SearchCatalogFunctionArn: this.searchCatalog,
      GetProductDetailFunctionArn: this.getProductDetail,
      AddToCartFunctionArn: this.addToCart,
      RemoveFromCartFunctionArn: this.removeFromCart,
      PlaceOrderFunctionArn: this.placeOrder,
      CatalogIndexWriterFunctionArn: this.catalogIndexWriter,
    };
    for (const [outputId, fn] of Object.entries(outputs)) {
      new cdk.CfnOutput(this, outputId, { value: fn.functionArn, exportName: `VoiceShop-LambdaStack-${outputId}` });
    }
  }
}

function kebab(name: string): string {
  return name.replace(/([a-z])([A-Z])/g, '$1-$2').toLowerCase();
}
