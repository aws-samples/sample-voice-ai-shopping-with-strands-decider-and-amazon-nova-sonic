import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

/** Same content tag as container/buildspec.yml: sm-<sha256(Dockerfile + requirements.txt + serve.py)[:12]>. */
export function deciderImageTag(containerDir: string): string {
  const hash = createHash('sha256');
  for (const file of ['Dockerfile', 'requirements.txt', 'serve.py']) {
    hash.update(readFileSync(join(containerDir, file)));
  }
  return `sm-${hash.digest('hex').slice(0, 12)}`;
}
