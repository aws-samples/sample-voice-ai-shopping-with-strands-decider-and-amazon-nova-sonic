import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as bedrockagentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as iam from 'aws-cdk-lib/aws-iam';

export interface AgentCoreRuntimeStackProps extends cdk.StackProps {
  readonly repository: ecr.IRepository;
  readonly imageTag: string;
  readonly runtimeRole: iam.IRole;
  readonly memoryId: string;
  readonly gatewayUrl: string;
  readonly deciderEndpointName: string;
  /** Cognito user pool and app client whose access tokens may open a session. */
  readonly userPoolId: string;
  readonly userPoolClientId: string;
  readonly sonicModelId: string;
  readonly actThreshold: number;
  readonly askThreshold: number;
}

/**
 * VoiceShop-AgentCoreRuntimeStack: the session host as an AgentCore Runtime on the
 * microVM compute type (one microVM per shopping session), HTTP protocol, which
 * carries the browser's SigV4 WebSocket (/ws). Uses the L1 CfnRuntime like the
 * template; per-question thresholds ship in the image (calibration.json).
 */
export class AgentCoreRuntimeStack extends cdk.Stack {
  public readonly runtime: bedrockagentcore.CfnRuntime;

  constructor(scope: Construct, id: string, props: AgentCoreRuntimeStackProps) {
    super(scope, id, props);

    this.runtime = new bedrockagentcore.CfnRuntime(this, 'SessionHostRuntime', {
      agentRuntimeName: 'voiceshop_session_host',
      description: 'VoiceShop session host: Nova Sonic bridge, turn pipeline, decider client.',
      roleArn: props.runtimeRole.roleArn,
      agentRuntimeArtifact: { containerConfiguration: { containerUri: `${props.repository.repositoryUri}:${props.imageTag}` } },
      networkConfiguration: { networkMode: 'PUBLIC' },
      protocolConfiguration: 'HTTP',
      environmentVariables: {
        AGENTCORE_GATEWAY_URL: props.gatewayUrl,
        DECIDER_ENDPOINT_NAME: props.deciderEndpointName,
        SONIC_MODEL_ID: props.sonicModelId,
        MEMORY_ID: props.memoryId,
        ACT_THRESHOLD: String(props.actThreshold),
        ASK_THRESHOLD: String(props.askThreshold),
        SESSIONS_TABLE_NAME: 'VoiceShop-Sessions',
        PRODUCTS_TABLE_NAME: 'VoiceShop-Products',
        LOG_LEVEL: 'INFO',
        // Access tokens are accepted only from this user pool and app client (jwt_auth.check_audience).
        USER_POOL_ID: props.userPoolId,
        USER_POOL_CLIENT_ID: props.userPoolClientId,
      },
    });

    new cdk.CfnOutput(this, 'AgentRuntimeArn', {
      value: this.runtime.attrAgentRuntimeArn,
      exportName: 'VoiceShop-AgentCoreRuntimeStack-AgentRuntimeArn',
    });
  }
}
