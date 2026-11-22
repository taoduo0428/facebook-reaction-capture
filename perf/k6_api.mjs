// Only the launcher renders this sentinel in its private temporary directory.
// Direct invocation stops during init, before any HTTP call.
import http from 'k6/http';
import {check} from 'k6';
import exec from 'k6/execution';
import {Trend} from 'k6/metrics';

const fixture = null /* OWNED_FIXTURE */;
if (!fixture || !Number.isInteger(fixture.port) || fixture.port < 1 || fixture.port > 65535 ||
    !/^[a-f0-9]{32}$/.test(fixture.key) || !/^[a-f0-9]{32}$/.test(fixture.id) ||
    !/^fb_[a-f0-9]{16}$/.test(fixture.seed)) throw new Error('use python -m perf.run_api_load --driver k6');
const base = `http://127.0.0.1:${fixture.port}`;
const trends = {read: new Trend('read_latency', true), submit: new Trend('submit_latency', true)};
const paths = ['/health', '/v1/jobs/latest', '/metrics', `/v1/jobs/${fixture.seed}/progress`];
export const options = {
  scenarios: {read: scenario(9), submit: scenario(3)},
  maxRedirects: 0, noConnectionReuse: true,
  summaryTrendStats: ['count', 'p(50)', 'p(95)', 'p(99)'],
  thresholds: {checks: ['rate==1'], http_req_failed: ['rate==0'], dropped_iterations: ['count==0']},
};
function scenario(rate) {
  return {executor: 'constant-arrival-rate', exec: rate === 9 ? 'read' : 'submit', rate,
          timeUnit: '1s', duration: '8s', preAllocatedVUs: 1, maxVUs: 1, gracefulStop: '2s'};
}
function measure(name, path, payload, status) {
  const options = {headers: {'X-Fixture-Key': fixture.key, 'Content-Type': 'application/json'}, redirects: 0, timeout: '1s'};
  const r = payload ? http.post(base + path, payload, options) : http.get(base + path, options);
  trends[name].add(r.timings.duration);
  check(r, {[name]: res => res.status === status && res.headers['X-Fixture-Id'] === fixture.id &&
    (name !== 'submit' || /^fb_[a-f0-9]{16}$/.test(res.json('job_id')))});
}
export function read() {
  const i = exec.scenario.iterationInTest;
  if (i < 72) measure('read', paths[i % 4], null, 200);
}
export function submit() {
  if (exec.scenario.iterationInTest < 24) measure('submit', '/v1/jobs', JSON.stringify({post_url: 'fixture:synthetic'}), 202);
}
export function handleSummary(data) {
  return {'summary.json': JSON.stringify({metrics: data.metrics, root_group: data.root_group, elapsed_ms: data.state.testRunDurationMs})};
}
