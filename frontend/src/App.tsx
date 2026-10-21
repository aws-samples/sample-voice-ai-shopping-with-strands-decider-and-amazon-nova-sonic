import { useMemo, useState } from 'react';
import { Amplify } from 'aws-amplify';
import { fetchAuthSession } from 'aws-amplify/auth';
import { AgentCoreTransport, newRuntimeSessionId } from './services/AgentCoreTransport';
import { loadConfig, type AppConfig, type AwsCredentials } from './services/config';
import { ShopPage } from './components/ShopPage';
import { AuthScreen } from './components/AuthScreen';

const config = loadConfig();

// Configure Amplify before the first render: the Authenticator reads the saved
// session on mount, and configuring later (in an effect) made it show the sign-in
// form to a signed-in user ("There is already a signed in user").
Amplify.configure({
  Auth: {
    Cognito: {
      userPoolId: config.userPoolId,
      userPoolClientId: config.userPoolClientId,
      identityPoolId: config.identityPoolId,
      loginWith: { username: true, email: true },
    },
  },
});

export default function App() {
  return <LiveShop config={config} />;
}

function LiveShop({ config }: { config: AppConfig }) {
  return (
    <AuthScreen>
      {(user, signOut) => <SignedInShop config={config} userName={user.username} onSignOut={signOut} />}
    </AuthScreen>
  );
}

function SignedInShop({ config, userName, onSignOut }: { config: AppConfig; userName: string; onSignOut: () => void }) {
  // One runtime session id per shopping session, reused on reconnect, so the host restores the session.
  const [runtimeSessionId] = useState(newRuntimeSessionId);
  const transport = useMemo(() => {
    // Amplify exchanges the ID token for Identity Pool credentials (the
    // authenticated role may only open the session host WebSocket).
    const identityCredentials = async (): Promise<AwsCredentials> => {
      const { credentials } = await fetchAuthSession();
      if (!credentials) throw new Error('Not signed in');
      return {
        accessKeyId: credentials.accessKeyId,
        secretAccessKey: credentials.secretAccessKey,
        sessionToken: credentials.sessionToken ?? '',
        expiration: credentials.expiration ?? new Date(Date.now() + 3600_000),
      };
    };
    return new AgentCoreTransport({
      runtimeArn: config.runtimeArn,
      region: config.region,
      runtimeSessionId,
      credentials: identityCredentials,
      accessToken: async () => (await fetchAuthSession()).tokens?.accessToken?.toString() ?? '',
    });
  }, [config, runtimeSessionId]);

  return <ShopPage transport={transport} userName={userName} onSignOut={onSignOut} />;
}
