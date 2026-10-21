/**
 * Cognito sign-in (adapted from the template's AuthComponent): Amplify
 * Authenticator with sign-up hidden, and the NEW_PASSWORD_REQUIRED challenge on
 * first login handled by the Authenticator itself.
 */
import { Authenticator } from '@aws-amplify/ui-react';
import '@aws-amplify/ui-react/styles.css';
import { BagIcon, CartIcon, MicIcon, SparkIcon } from './Icons';

export function AuthScreen({ children }: { children: (user: { username: string }, signOut: () => void) => JSX.Element }) {
  return (
    <Authenticator.Provider>
      <AuthGate>{children}</AuthGate>
    </Authenticator.Provider>
  );
}

function AuthGate({ children }: { children: (user: { username: string }, signOut: () => void) => JSX.Element }) {
  return (
    <Authenticator
      hideSignUp
      className="auth"
      formFields={{ signIn: { username: { label: 'Username or email', placeholder: 'AppUser or your email' } } }}
      components={{
        Header: () => (
          <div className="auth__brand">
            <span className="brand__mark brand__mark--lg"><BagIcon size={22} /></span>
            <h1>VoiceShop</h1>
            <p className="muted">Sign in to start shopping by voice</p>
          </div>
        ),
        Footer: () => (
          <div className="auth__footer">
            <p><MicIcon size={14} /> Say what you want. The store narrows as you talk.</p>
            <p><SparkIcon size={14} /> Every pick comes with its reasons.</p>
            <p><CartIcon size={14} /> Add to cart and check out without touching the screen.</p>
            <p className="muted">First sign-in? Use the temporary password from your invitation email.</p>
          </div>
        ),
      }}
    >
      {({ user, signOut }) => children({ username: user?.username ?? '' }, () => signOut?.())}
    </Authenticator>
  );
}
