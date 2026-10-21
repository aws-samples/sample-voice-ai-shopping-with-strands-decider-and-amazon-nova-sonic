import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as iam from 'aws-cdk-lib/aws-iam';
import { NagSuppressions } from 'cdk-nag';

/**
 * Agent runtime name the session host is created with. AgentCore
 * appends a random suffix to form the runtime id, so the authenticated role
 * below is scoped to `runtime/<name>-*` (covering its endpoints too).
 */
export const SESSION_HOST_RUNTIME_NAME = 'voiceshop_session_host';

/**
 * User Pool, Identity Pool and the one demo test user (adapted from the
 * template repo's CognitoStack). The authenticated role can only open the
 * session host's SigV4 WebSocket; the browser calls nothing else in AWS.
 */
export class CognitoStack extends cdk.Stack {
  public readonly userPool: cognito.UserPool;
  public readonly userPoolClient: cognito.UserPoolClient;
  public readonly identityPool: cognito.CfnIdentityPool;
  public readonly authenticatedRole: iam.Role;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const userEmail = new cdk.CfnParameter(this, 'UserEmail', {
      type: 'String',
      description: 'Email address of the demo test user (receives the temporary password).',
      allowedPattern: '^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$',
      constraintDescription: 'Must be a valid email address',
    });
    const userName = new cdk.CfnParameter(this, 'UserName', {
      type: 'String',
      description: 'Full name of the demo test user (the sign-in username is always AppUser).',
      default: 'AppUser',
      minLength: 1,
    });

    this.userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: 'VoiceShop-UserPool',
      selfSignUpEnabled: false,
      signInAliases: { email: true, username: true },
      autoVerify: { email: true },
      userInvitation: {
        emailSubject: 'VoiceShop demo: your account is ready',
        emailBody:
          'Hello {username}, your VoiceShop demo account is ready.<br/><br/>' +
          'Username: <b>{username}</b><br/>Temporary password: <b>{####}</b><br/><br/>' +
          'You will be asked to set a new password at first sign-in.',
      },
      standardAttributes: {
        email: { required: true, mutable: true },
        fullname: { required: true, mutable: true },
      },
      passwordPolicy: {
        minLength: 8,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
      },
      standardThreatProtectionMode: cognito.StandardThreatProtectionMode.FULL_FUNCTION,
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.userPoolClient = this.userPool.addClient('WebClient', {
      userPoolClientName: 'VoiceShop-WebClient',
      authFlows: { userPassword: true, userSrp: true },
      generateSecret: false, // public browser client
      preventUserExistenceErrors: true,
    });

    this.identityPool = new cognito.CfnIdentityPool(this, 'IdentityPool', {
      identityPoolName: 'VoiceShop-IdentityPool',
      allowUnauthenticatedIdentities: false,
      cognitoIdentityProviders: [
        {
          clientId: this.userPoolClient.userPoolClientId,
          providerName: this.userPool.userPoolProviderName,
        },
      ],
    });

    this.authenticatedRole = new iam.Role(this, 'AuthenticatedRole', {
      assumedBy: new iam.FederatedPrincipal(
        'cognito-identity.amazonaws.com',
        {
          StringEquals: { 'cognito-identity.amazonaws.com:aud': this.identityPool.ref },
          'ForAnyValue:StringLike': { 'cognito-identity.amazonaws.com:amr': 'authenticated' },
        },
        'sts:AssumeRoleWithWebIdentity',
      ),
      description: 'VoiceShop authenticated shoppers: open the session host WebSocket only.',
    });
    // Action name as used by the template's identity pool role.
    this.authenticatedRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStream'],
        resources: [
          `arn:${cdk.Aws.PARTITION}:bedrock-agentcore:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:runtime/${SESSION_HOST_RUNTIME_NAME}-*`,
        ],
      }),
    );

    new cognito.CfnIdentityPoolRoleAttachment(this, 'IdentityPoolRoleAttachment', {
      identityPoolId: this.identityPool.ref,
      roles: { authenticated: this.authenticatedRole.roleArn },
    });

    const testUser = new cognito.CfnUserPoolUser(this, 'TestUser', {
      userPoolId: this.userPool.userPoolId,
      username: 'AppUser',
      userAttributes: [
        { name: 'email', value: userEmail.valueAsString },
        { name: 'email_verified', value: 'true' },
        { name: 'name', value: userName.valueAsString },
      ],
      desiredDeliveryMediums: ['EMAIL'],
    });
    void testUser;

    NagSuppressions.addResourceSuppressions(this.userPool, [
      {
        id: 'AwsSolutions-COG2',
        reason: 'MFA is not enabled for this single-user demo sample; the README tells deployers to enable it for anything beyond development and recording.',
      },
      {
        id: 'AwsSolutions-COG8',
        reason: 'The Plus feature plan is a paid per-user tier; this single-user demo uses the Essentials plan with standard threat protection.',
      },
    ]);
    NagSuppressions.addResourceSuppressions(
      this.authenticatedRole,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Scoped to the one session host runtime by name; the trailing wildcard covers the random id suffix AgentCore appends and the runtime\'s endpoints.',
        },
      ],
      true,
    );

    new cdk.CfnOutput(this, 'UserPoolId', { value: this.userPool.userPoolId, exportName: 'VoiceShop-CognitoStack-UserPoolId' });
    new cdk.CfnOutput(this, 'UserPoolClientId', { value: this.userPoolClient.userPoolClientId, exportName: 'VoiceShop-CognitoStack-UserPoolClientId' });
    new cdk.CfnOutput(this, 'IdentityPoolId', { value: this.identityPool.ref, exportName: 'VoiceShop-CognitoStack-IdentityPoolId' });
    new cdk.CfnOutput(this, 'AuthenticatedRoleArn', { value: this.authenticatedRole.roleArn, exportName: 'VoiceShop-CognitoStack-AuthenticatedRoleArn' });
  }
}
