/**
 * Runtime configuration from Vite env values, generated into .env.local from the
 * deployed stacks' outputs by scripts/generate-config.js. The app only runs
 * against the deployed AWS backend.
 */

export interface AppConfig {
  region: string;
  userPoolId: string;
  userPoolClientId: string;
  identityPoolId: string;
  runtimeArn: string;
}

export function loadConfig(): AppConfig {
  const env = import.meta.env;
  const missing = ['VITE_USER_POOL_ID', 'VITE_CLIENT_ID', 'VITE_IDENTITY_POOL_ID', 'VITE_RUNTIME_ARN'].filter((k) => !env[k]);
  if (missing.length) throw new Error(`Missing ${missing.join(', ')}: run scripts/generate-config.js after deploy-all.sh`);
  return {
    region: env.VITE_REGION || 'us-east-1',
    userPoolId: env.VITE_USER_POOL_ID || '',
    userPoolClientId: env.VITE_CLIENT_ID || '',
    identityPoolId: env.VITE_IDENTITY_POOL_ID || '',
    runtimeArn: env.VITE_RUNTIME_ARN || '',
  };
}

export interface AwsCredentials {
  accessKeyId: string;
  secretAccessKey: string;
  sessionToken: string;
  expiration: Date;
}
