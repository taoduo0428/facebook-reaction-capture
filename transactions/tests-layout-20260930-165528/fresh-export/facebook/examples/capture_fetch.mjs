// Node.js 22+; no npm dependencies. Submit once or resume an existing job.
import { writeFile } from 'node:fs/promises';
import { parseArgs } from 'node:util';

const { values: args } = parseArgs({ options: {
  url: { type: 'string' }, 'job-id': { type: 'string' },
  'base-url': { type: 'string', default: 'http://127.0.0.1:8765' },
  timeout: { type: 'string', default: '1800' },
  output: { type: 'string', default: 'reactors.csv' },
} });
if (Boolean(args.url) === Boolean(args['job-id'])) throw Error('Use either --url or --job-id');
const timeout = Number(args.timeout);
if (!Number.isFinite(timeout) || timeout <= 0) throw Error('--timeout must be finite and positive');
const base = args['base-url'].replace(/\/$/, '');
async function request(path, body) {
  const response = await fetch(base + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(30000),
  });
  if (!response.ok) throw Error(`HTTP ${response.status}: ${await response.text()}`);
  return response;
}
await request('/health');
const id = args['job-id'] || (await (await request('/v1/jobs', {
  post_url: args.url, collect: ['reactions'], save_artifacts: ['progress', 'safe_capture'],
})).json()).job_id;
console.log(`job_id=${id}`);
const path = `/v1/jobs/${id}`;
const deadline = performance.now() + timeout * 1000;
while (true) {
  const job = await (await request(path)).json();
  if (job.status === 'completed') break;
  if (['failed', 'cancelled'].includes(job.status)) throw Error(JSON.stringify(job));
  if (!['queued', 'running', 'cancelling'].includes(job.status)) throw Error(`Unknown status: ${job.status}`);
  console.log(JSON.stringify(await (await request(path + '/progress')).json()));
  const remaining = deadline - performance.now();
  if (remaining <= 0) throw Error(`Resume with --job-id ${id}; job was not cancelled`);
  await new Promise(resolve => setTimeout(resolve, Math.min(2000, remaining)));
}
const result = await (await request(path + '/result')).json();
const data = await (await request(path + '/artifacts/facebook-reactor-users.csv?download=1')).arrayBuffer();
await writeFile(args.output, Buffer.from(data));
console.log(JSON.stringify(result, null, 2));
