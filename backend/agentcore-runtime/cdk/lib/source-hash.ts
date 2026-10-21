import { createHash } from 'node:crypto';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

/** Content tag for the session host image: sha256 over the files the Dockerfile copies. */
export function sourceTag(agentDir: string): string {
  const files = readdirSync(agentDir)
    .filter((f) => f.endsWith('.py') || ['requirements.txt', 'calibration.json', 'Dockerfile'].includes(f))
    .sort();
  const hash = createHash('sha256');
  for (const f of files) {
    hash.update(f);
    hash.update(readFileSync(join(agentDir, f)));
  }
  return `host-${hash.digest('hex').slice(0, 12)}`;
}
