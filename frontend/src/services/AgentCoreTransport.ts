/**
 * SessionTransport for the real session host: a SigV4-presigned WebSocket to the
 * AgentCore Runtime (adapted from the template's WebSocketClient). The first
 * message is always `auth` with the Cognito access token (the host
 * closes the socket with 4401 otherwise). The runtime session id is created once
 * per shopping session and reused on reconnect, so the host restores state.
 */

import { SignatureV4 } from '@smithy/signature-v4';
import { Sha256 } from '@aws-crypto/sha256-js';
import type { AwsCredentials } from './config';
import type { BrowserMessage, ConnectionState, HostMessage, SessionTransport } from './protocol';

const MAX_RECONNECTS = 5;
const AUTH_FAILED_CLOSE_CODE = 4401;
// Too many open sessions for this account (session host): retrying would only be refused again.
const TOO_MANY_SESSIONS_CLOSE_CODE = 4429;

/** UUID v4 plus a timestamp suffix: at least 33 characters, as AgentCore requires. */
export function newRuntimeSessionId(): string {
  return `${crypto.randomUUID()}-${Date.now().toString(36)}`;
}

export interface AgentCoreTransportOptions {
  runtimeArn: string;
  region: string;
  credentials: () => Promise<AwsCredentials>;
  accessToken: () => Promise<string>;
  runtimeSessionId: string;
}

export class AgentCoreTransport implements SessionTransport {
  private ws: WebSocket | null = null;
  private reconnects = 0;
  private closedByUser = false;
  // Bumped by every connect() and disconnect(): a connect() that was superseded while
  // it awaited signing must not open a socket (React StrictMode runs effects twice).
  private generation = 0;
  private messageHandler: (message: HostMessage) => void = () => {};
  private connectionHandler: (state: ConnectionState) => void = () => {};

  constructor(private readonly options: AgentCoreTransportOptions) {}

  onMessage(handler: (message: HostMessage) => void): void {
    this.messageHandler = handler;
  }

  onConnectionChange(handler: (state: ConnectionState) => void): void {
    this.connectionHandler = handler;
  }

  async connect(): Promise<void> {
    const generation = ++this.generation;
    this.closedByUser = false;
    this.connectionHandler(this.reconnects ? 'reconnecting' : 'connecting');
    const url = await this.presignedUrl();
    const accessToken = await this.options.accessToken();
    if (generation !== this.generation || this.closedByUser) return;
    const ws = new WebSocket(url);
    this.ws = ws;

    ws.onopen = () => {
      this.reconnects = 0;
      ws.send(JSON.stringify({ type: 'auth', access_token: accessToken } satisfies BrowserMessage));
      this.connectionHandler('connected');
    };
    ws.onmessage = (event) => {
      try {
        this.messageHandler(JSON.parse(event.data) as HostMessage);
      } catch (err) {
        console.warn('⚠️ Dropped unparseable host message', err);
      }
    };
    ws.onclose = (event) => {
      if (this.ws !== ws) return;   // an older socket closing late must not touch the new one
      this.ws = null;
      const retry = !this.closedByUser && event.code !== 1000 && event.code !== AUTH_FAILED_CLOSE_CODE
        && event.code !== TOO_MANY_SESSIONS_CLOSE_CODE
        && this.reconnects < MAX_RECONNECTS;
      if (event.code === AUTH_FAILED_CLOSE_CODE) {
        this.messageHandler({ type: 'error', message: 'Your sign-in could not be verified. Please sign in again.' });
      }
      if (retry) {
        this.reconnects += 1;
        this.connectionHandler('reconnecting');
        setTimeout(() => this.connect().catch((err) => this.failed(err)), 1000 * this.reconnects);
      } else {
        this.connectionHandler('disconnected');
      }
    };
  }

  /** Signing or sign-in failed before a socket existed: say so instead of waiting forever. */
  failed(err: unknown): void {
    console.error('❌ Could not connect to the store', err);
    this.connectionHandler('disconnected');
    this.messageHandler({ type: 'error', message: 'Could not connect to the store. Please refresh the page.' });
  }

  send(message: BrowserMessage): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    }
  }

  disconnect(): void {
    this.generation += 1;
    this.closedByUser = true;
    this.ws?.close(1000, 'Client disconnect');
    this.ws = null;
  }

  /** wss://bedrock-agentcore.<region>.amazonaws.com/runtimes/<arn>/ws, SigV4 query-signed. */
  private async presignedUrl(): Promise<string> {
    const { runtimeArn, region, runtimeSessionId } = this.options;
    const credentials = await this.options.credentials();
    const hostname = `bedrock-agentcore.${region}.amazonaws.com`;
    const signer = new SignatureV4({
      service: 'bedrock-agentcore',
      region,
      credentials: {
        accessKeyId: credentials.accessKeyId,
        secretAccessKey: credentials.secretAccessKey,
        sessionToken: credentials.sessionToken,
      },
      sha256: Sha256,
    });
    const signed = await signer.presign(
      {
        method: 'GET',
        protocol: 'https:',
        hostname,
        // Raw ARN in the path, as in the template; the signer escapes it.
        path: `/runtimes/${runtimeArn}/ws`,
        query: {
          qualifier: 'DEFAULT',
          // Session id as a signed query parameter (browser WebSockets cannot set
          // headers); verified end to end against the session host.
          'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': runtimeSessionId,
        },
        headers: { host: hostname },
      },
      { expiresIn: 300 },
    );
    const query = new URLSearchParams(signed.query as Record<string, string>).toString();
    return `wss://${hostname}${signed.path}?${query}`;
  }
}
