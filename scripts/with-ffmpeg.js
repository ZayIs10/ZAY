#!/usr/bin/env node
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const ffmpeg = require('@ffmpeg-installer/ffmpeg');
const ffprobe = require('@ffprobe-installer/ffprobe');

const sep = process.platform === 'win32' ? ';' : ':';
const ffmpegDir = path.dirname(ffmpeg.path);
const ffprobeDir = path.dirname(ffprobe.path);
const localBin = path.join(__dirname, '..', 'node_modules', '.bin');

const env = {
  ...process.env,
  PATH: `${localBin}${sep}${ffmpegDir}${sep}${ffprobeDir}${sep}${process.env.PATH ?? ''}`,
  FFMPEG_PATH: ffmpeg.path,
  FFPROBE_PATH: ffprobe.path,
};

const [, , bin, ...args] = process.argv;
if (!bin) {
  console.error('Usage: with-ffmpeg <command> [args...]');
  process.exit(2);
}

const isWin = process.platform === 'win32';
const exe = isWin ? path.join(localBin, `${bin}.cmd`) : path.join(localBin, bin);

const result = isWin
  ? spawnSync(`"${exe}" ${args.map(a => `"${a}"`).join(' ')}`, {
      env, stdio: 'inherit', shell: true,
    })
  : spawnSync(exe, args, { env, stdio: 'inherit' });
process.exit(result.status ?? 1);
