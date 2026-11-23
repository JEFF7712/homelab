import { readdir, lstat } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { resolve, join } from 'node:path';
import { pathToFileURL } from 'node:url';

const run = promisify(execFile);

// NFS creates on this NAS do not reliably update parent directory mtimes.
// Use touch's UTIME_NOW rather than explicit timestamps: group write access
// permits UTIME_NOW even when the shared root belongs to another UID.
export async function refreshDirectoryTimes(root, excluded) {
  if (!(await lstat(root)).isDirectory()) {
    throw new Error(`Import root must be a directory, not a symlink: ${root}`);
  }
  let count = 0;
  async function visit(directory, top = false) {
    await run('touch', ['-c', '--', directory]);
    count += 1;
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      if (!entry.isDirectory() || entry.name.startsWith('.')) continue;
      if (top && excluded.has(entry.name)) continue;
      await visit(join(directory, entry.name));
    }
  }
  await visit(root, true);
  return count;
}

async function main() {
  const { readRommSettings, rommClient } = await import('/app/dist/server/romm.js');
  const settings = await readRommSettings();
  if (!settings.enabled || !settings.scanAfterImport || !settings.apiToken) {
    console.log('RomM integration disabled; reconciliation skipped');
    return;
  }
  const excluded = new Set(JSON.parse(process.env.EXCLUDED_PLATFORMS));
  const count = await refreshDirectoryTimes(process.env.ROMM_IMPORT_ROOT, excluded);
  await rommClient.triggerScan(settings);
  console.log(`Refreshed ${count} console directories and queued a RomM quick scan`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().then(() => process.exit(0)).catch((error) => {
    console.error(`RomM library reconciliation failed: ${error.message}`);
    process.exit(1);
  });
}
