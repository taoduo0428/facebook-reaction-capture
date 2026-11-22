// k6 API load profile for the FastAPI backend.
//
// Two scenarios run together:
//   reads   - constant VUs polling /health, /v1/jobs/latest and /metrics
//   submits - constant arrival rate of POST /v1/jobs (202) followed by status
//             and progress reads, i.e. what the UI and the Python client do
//
// Thresholds are deliberately about the HTTP layer, not about capture work: the
// target server (perf/fake_server.py) uses an in-memory manager and no device.
//
//   k6 run -e BASE_URL=http://127.0.0.1:8799 perf/k6_api.js
import http from 'k6/http';
import { check } from 'k6';
import { Trend } from 'k6/metrics';

const BASE = __ENV.BASE_URL || 'http://127.0.0.1:8799';
const readLatency = new Trend('read_latency', true);
const submitLatency = new Trend('submit_latency', true);

export const options = {
  scenarios: {
    reads: {
      executor: 'constant-vus',
      vus: Number(__ENV.READ_VUS || 8),
      duration: __ENV.READ_DURATION || '10s',
      exec: 'readScenario',
      tags: { scenario: 'reads' },
    },
    submits: {
      executor: 'constant-arrival-rate',
      rate: Number(__ENV.SUBMIT_RATE || 20),
      timeUnit: '1s',
      duration: __ENV.SUBMIT_DURATION || '10s',
      preAllocatedVUs: Number(__ENV.SUBMIT_PRE_VUS || 10),
      maxVUs: Number(__ENV.SUBMIT_MAX_VUS || 50),
      exec: 'submitScenario',
      tags: { scenario: 'submits' },
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    'http_req_duration{scenario:reads}': ['p(95)<100'],
    'http_req_duration{scenario:submits}': ['p(95)<250'],
    read_latency: ['p(95)<100'],
    submit_latency: ['p(95)<250'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(50)', 'p(90)', 'p(95)', 'p(99)', 'max', 'count'],
};

export function readScenario() {
  const health = http.get(`${BASE}/health`, { tags: { name: 'GET /health' } });
  readLatency.add(health.timings.duration);
  check(health, { 'health is 200': (r) => r.status === 200 });

  const latest = http.get(`${BASE}/v1/jobs/latest`, { tags: { name: 'GET /v1/jobs/latest' } });
  readLatency.add(latest.timings.duration);
  check(latest, { 'latest is 200 or 404': (r) => r.status === 200 || r.status === 404 });

  const metrics = http.get(`${BASE}/metrics`, { tags: { name: 'GET /metrics' } });
  readLatency.add(metrics.timings.duration);
  check(metrics, { 'metrics is 200': (r) => r.status === 200 });
}

export function submitScenario() {
  const payload = JSON.stringify({
    post_url: 'https://www.facebook.com/x/posts/1',
    collect: ['reactions'],
    save_artifacts: ['safe_capture', 'progress'],
  });
  const response = http.post(`${BASE}/v1/jobs`, payload, {
    headers: { 'Content-Type': 'application/json' },
    tags: { name: 'POST /v1/jobs' },
  });
  submitLatency.add(response.timings.duration);
  const accepted = check(response, { 'submit is 202': (r) => r.status === 202 });
  if (!accepted) {
    return;
  }
  const jobId = response.json('job_id');
  if (!jobId) {
    return;
  }
  const status = http.get(`${BASE}/v1/jobs/${jobId}`, { tags: { name: 'GET /v1/jobs/{id}' } });
  check(status, { 'status is 200': (r) => r.status === 200 });
  const progress = http.get(`${BASE}/v1/jobs/${jobId}/progress`, {
    tags: { name: 'GET /v1/jobs/{id}/progress' },
  });
  check(progress, { 'progress is 200': (r) => r.status === 200 });
}
