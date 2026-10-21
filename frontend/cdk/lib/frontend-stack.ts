import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as amplify from 'aws-cdk-lib/aws-amplify';

/**
 * VoiceShop-FrontendStack: an Amplify Hosting app with one `main` branch, deployed
 * manually from a local build (`npm run deploy:amplify`), as in the template. The
 * site is public, but shows nothing until the shopper signs in with Cognito; all
 * data comes from the session host over the SigV4 WebSocket.
 */
export class FrontendStack extends cdk.Stack {
  public readonly appId: string;
  public readonly appUrl: string;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const app = new amplify.CfnApp(this, 'FrontendApp', {
      name: 'voiceshop-frontend',
      description: 'VoiceShop shopping UI (React + Vite), manual deployments.',
      platform: 'WEB',
      // Single-page app: every path without a file extension serves index.html.
      customRules: [
        {
          source: '</^[^.]+$|\\.(?!(css|gif|ico|jpg|js|png|txt|svg|woff|woff2|ttf|map|json|webp)$)([^.]+$)/>',
          target: '/index.html',
          status: '200',
        },
      ],
      customHeaders: [
        'customHeaders:',
        "  - pattern: '**'",
        '    headers:',
        "      - key: 'Strict-Transport-Security'",
        "        value: 'max-age=31536000; includeSubDomains'",
        "      - key: 'X-Content-Type-Options'",
        "        value: 'nosniff'",
        "      - key: 'X-Frame-Options'",
        "        value: 'DENY'",
        "      - key: 'Referrer-Policy'",
        "        value: 'strict-origin-when-cross-origin'",
        "      - key: 'Permissions-Policy'",
        "        value: 'microphone=(self), camera=(), geolocation=()'",
      ].join('\n'),
    });

    new amplify.CfnBranch(this, 'MainBranch', {
      appId: app.attrAppId,
      branchName: 'main',
      enableAutoBuild: false,
      stage: 'PRODUCTION',
    });

    this.appId = app.attrAppId;
    this.appUrl = `https://main.${app.attrDefaultDomain}`;

    new cdk.CfnOutput(this, 'AmplifyAppId', { value: this.appId, exportName: 'VoiceShop-FrontendStack-AmplifyAppId' });
    new cdk.CfnOutput(this, 'AmplifyAppUrl', { value: this.appUrl, exportName: 'VoiceShop-FrontendStack-AmplifyAppUrl' });
  }
}
