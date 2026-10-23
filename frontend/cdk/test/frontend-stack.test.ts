import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { FrontendStack } from '../lib/frontend-stack';

const template = Template.fromStack(new FrontendStack(new cdk.App(), 'TestFrontend', {
  env: { account: '111111111111', region: 'us-east-1' },
}));

test('Amplify app with a manually deployed main branch', () => {
  template.resourceCountIs('AWS::Amplify::App', 1);
  template.hasResourceProperties('AWS::Amplify::Branch', { BranchName: 'main', EnableAutoBuild: false });
});

test('SPA rewrite and security headers', () => {
  const app = Object.values(template.findResources('AWS::Amplify::App'))[0].Properties;
  expect(app.CustomRules[0].Target).toBe('/index.html');
  for (const header of ['Strict-Transport-Security', 'X-Content-Type-Options', 'X-Frame-Options', 'Referrer-Policy',
                        'Content-Security-Policy']) {
    expect(app.CustomHeaders).toContain(header);
  }
  for (const source of ["script-src 'self'", "frame-ancestors 'none'", "object-src 'none'",
                        'https://voiceshop-images-111111111111-us-east-1.s3.us-east-1.amazonaws.com',
                        'wss://bedrock-agentcore.us-east-1.amazonaws.com', 'https://cognito-idp.us-east-1.amazonaws.com']) {
    expect(app.CustomHeaders).toContain(source);
  }
});
