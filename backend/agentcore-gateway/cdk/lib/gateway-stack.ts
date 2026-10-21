import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as agentcore from '@aws-cdk/aws-bedrock-agentcore-alpha';
import { NagSuppressions } from 'cdk-nag';
import { TOOLS } from './tool-schemas';

export interface GatewayStackProps extends cdk.StackProps {
  /** Tool name -> Lambda function ARN (from VoiceShop-LambdaStack exports). */
  readonly toolFunctionArns: Record<string, string>;
}

export const GATEWAY_NAME = 'voiceshop-gateway';

/**
 * VoiceShop-AgentCoreGatewayStack (ADR-12): one MCP
 * gateway with AWS_IAM inbound auth and one Lambda target per tool. Only the
 * session host calls it, with SigV4 from its runtime role. Gateway exposes each
 * tool as "<target>___<tool>"; the host resolves tools by suffix.
 *
 * Uses the alpha Gateway/GatewayTarget constructs (present in the pinned
 * @aws-cdk/aws-bedrock-agentcore-alpha 2.235.1-alpha.0),
 * not the template's custom-resource pattern.
 */
export class GatewayStack extends cdk.Stack {
  public readonly gateway: agentcore.Gateway;

  constructor(scope: Construct, id: string, props: GatewayStackProps) {
    super(scope, id, props);

    this.gateway = new agentcore.Gateway(this, 'Gateway', {
      gatewayName: GATEWAY_NAME,
      description: 'VoiceShop catalog and cart tools for the session host (MCP, IAM auth).',
      protocolConfiguration: agentcore.GatewayProtocol.mcp({
        instructions: 'VoiceShop shopping tools: catalog search, product detail, cart and order.',
      }),
      authorizerConfiguration: agentcore.GatewayAuthorizer.usingAwsIam(),
    });

    for (const tool of TOOLS) {
      // Same account and Region (sibling CDK app), so CDK may grant invoke on it.
      const fn = lambda.Function.fromFunctionAttributes(this, `${tool.targetName}Function`, {
        functionArn: props.toolFunctionArns[tool.definition.name],
        sameEnvironment: true,
      });
      this.gateway.addLambdaTarget(`${tool.targetName}Target`, {
        gatewayTargetName: tool.targetName,
        description: tool.definition.description,
        lambdaFunction: fn,
        toolSchema: agentcore.ToolSchema.fromInline([tool.definition]),
      });
    }

    NagSuppressions.addResourceSuppressions(
      this.gateway,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'The construct-generated gateway role grants lambda:InvokeFunction on the five tool function ARNs (plus their version/alias ":*" suffix); no other resources.',
        },
      ],
      true,
    );

    new cdk.CfnOutput(this, 'GatewayUrl', { value: this.gateway.gatewayUrl!, exportName: 'VoiceShop-AgentCoreGatewayStack-GatewayUrl' });
    new cdk.CfnOutput(this, 'GatewayArn', { value: this.gateway.gatewayArn, exportName: 'VoiceShop-AgentCoreGatewayStack-GatewayArn' });
    new cdk.CfnOutput(this, 'GatewayId', { value: this.gateway.gatewayId, exportName: 'VoiceShop-AgentCoreGatewayStack-GatewayId' });
  }
}
