import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { NagSuppressions } from 'cdk-nag';

/**
 * The four VoiceShop tables. All
 * PAY_PER_REQUEST, AWS-managed encryption, explicit DESTROY (demo stack).
 */
export class DynamoDBStack extends cdk.Stack {
  public readonly productsTable: dynamodb.Table;
  public readonly sessionsTable: dynamodb.Table;
  public readonly cartTable: dynamodb.Table;
  public readonly ordersTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const tableDefaults = {
      partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    };

    // =====================================================================
    // Products: PK = PRODUCT#<asin>, SK = META, plus the CATALOG#META item.
    // Written by the catalog load, read by the tool Lambdas and the session host.
    // =====================================================================
    this.productsTable = new dynamodb.Table(this, 'ProductsTable', {
      ...tableDefaults,
      tableName: 'VoiceShop-Products',
    });

    // =====================================================================
    // Sessions: PK = SESSION#<runtimeSessionId>, SK = STATE | TURN#<seq>.
    // Written by the session host; expires 24 h after the last write.
    // =====================================================================
    this.sessionsTable = new dynamodb.Table(this, 'SessionsTable', {
      ...tableDefaults,
      tableName: 'VoiceShop-Sessions',
      timeToLiveAttribute: 'expiresAt',
    });

    // =====================================================================
    // Cart: PK = CART#<sessionId>, SK = ITEM#<asin>, so add_to_cart is a
    // single-item upsert and place_order reads the whole cart with one Query.
    // Written only by the add-to-cart and place-order Lambdas.
    // =====================================================================
    this.cartTable = new dynamodb.Table(this, 'CartTable', {
      ...tableDefaults,
      tableName: 'VoiceShop-Cart',
      timeToLiveAttribute: 'expiresAt',
    });

    // =====================================================================
    // Orders: PK = ORDER#<orderId>, SK = META. orderId is derived from the
    // place_order idempotencyKey and written with attribute_not_exists(PK), so
    // a retried place_order returns the existing order instead of a second one.
    // =====================================================================
    this.ordersTable = new dynamodb.Table(this, 'OrdersTable', {
      ...tableDefaults,
      tableName: 'VoiceShop-Orders',
    });

    for (const table of [this.productsTable, this.sessionsTable, this.cartTable, this.ordersTable]) {
      NagSuppressions.addResourceSuppressions(table, [
        {
          id: 'AwsSolutions-DDB3',
          reason: 'Point-in-time recovery is off by design for this demo: the catalog is reloaded from the zip in the repository, and sessions, carts and orders are throwaway demo data.',
        },
      ]);
    }

    const outputs: Record<string, dynamodb.Table> = {
      ProductsTableName: this.productsTable,
      SessionsTableName: this.sessionsTable,
      CartTableName: this.cartTable,
      OrdersTableName: this.ordersTable,
    };
    for (const [outputId, table] of Object.entries(outputs)) {
      new cdk.CfnOutput(this, outputId, {
        value: table.tableName,
        exportName: `VoiceShop-DynamoDBStack-${outputId}`,
      });
    }
  }
}
