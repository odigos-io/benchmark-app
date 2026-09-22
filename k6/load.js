import http from 'k6/http';
import exec from 'k6/execution';
import { Trend, Counter } from 'k6/metrics';

// One endpoint, open-loop only. A closed loop (N VUs looping) lets a faster arm
// serve more requests, so CPU per request would compare two different points
// on the service's load curve instead of the same work with and without the
// agent. Every arm here is driven at the same fixed arrival rate.
const BASE_URL = __ENV.BASE_URL || 'http://bucket-app.cell-m.svc.cluster.local:8080';
const MODE = (__ENV.MODE || 'warmrun').toLowerCase();
const DURATION = __ENV.DURATION || '8m';
const RATE = Number(__ENV.RATE || '100');
const WARM_RAMP = __ENV.WARM_RAMP || '60s';
const WARM_S = Number(__ENV.WARM_S || '360');
const P99_S = Number(__ENV.P99_S || '0.2');

function seconds(d) {
  const m = String(d).match(/^(\d+)(ms|s|m|h)?$/);
  if (!m) throw new Error(`bad duration ${d}`);
  const n = Number(m[1]);
  return m[2] === 'ms' ? n / 1000 : m[2] === 'm' ? n * 60 : m[2] === 'h' ? n * 3600 : n;
}
const DURATION_S = seconds(DURATION);
const RAMP_S = seconds(WARM_RAMP);
const HOLD_S = Math.max(0, WARM_S - RAMP_S);

// VU pool from Little's law at the worst latency the cell should ever see: a
// pool that runs out does not slow the app, it silently lowers the arrival
// rate (dropped_iterations), and the gate would reject the arm.
const PRE_VUS = Number(__ENV.PRE_VUS || String(Math.max(50, Math.ceil(3 * RATE * P99_S))));
const MAX_VUS = Number(__ENV.MAX_VUS || String(2 * PRE_VUS));

// What every response must report. A wrong preset, an env override that did
// not take, or an op that silently short-circuited would otherwise measure a
// different application than the one named in the results.
const EXPECT = {
  units: __ENV.EXPECT_UNITS !== undefined && __ENV.EXPECT_UNITS !== '' ? Number(__ENV.EXPECT_UNITS) : null,
  jdbc: __ENV.EXPECT_JDBC !== undefined && __ENV.EXPECT_JDBC !== '' ? Number(__ENV.EXPECT_JDBC) : null,
  redis: __ENV.EXPECT_REDIS !== undefined && __ENV.EXPECT_REDIS !== '' ? Number(__ENV.EXPECT_REDIS) : null,
  http: __ENV.EXPECT_HTTP !== undefined && __ENV.EXPECT_HTTP !== '' ? Number(__ENV.EXPECT_HTTP) : null,
};

// Recorded in the measure phase only, so the summary describes the measured
// window and nothing else.
const latency = new Trend('lat_checkout', true);
const errors = new Counter('err_checkout');
const requests = new Counter('req_checkout');
const checkFail = new Counter('check_fail');
const warmRequests = new Counter('req_warm');

const TREND_STATS = ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'];

// MODE=warmrun  one continuous job: 0 -> RATE over WARM_RAMP, hold RATE until
//               WARM_S has elapsed, then hold RATE for DURATION. Requests in the
//               first WARM_S seconds are tagged phase=warm and not recorded. A
//               separate warm job followed by a pause and a fresh job gave the
//               measure window a cold VU pool and cold connections at its start,
//               visible as a CPU/req bump and disagreeing half-windows.
// MODE=rate     fixed arrival rate for DURATION, everything measured (calibration,
//               span capture).
export const options = {
  summaryTrendStats: TREND_STATS,
  discardResponseBodies: false,
  scenarios: MODE === 'rate'
    ? {
        checkout: {
          executor: 'constant-arrival-rate',
          rate: RATE,
          timeUnit: '1s',
          duration: DURATION,
          preAllocatedVUs: PRE_VUS,
          maxVUs: MAX_VUS,
        },
      }
    : {
        checkout: {
          executor: 'ramping-arrival-rate',
          startRate: 0,
          timeUnit: '1s',
          preAllocatedVUs: PRE_VUS,
          maxVUs: MAX_VUS,
          stages: [
            { duration: `${RAMP_S}s`, target: RATE },
            { duration: `${HOLD_S}s`, target: RATE },
            { duration: `${DURATION_S}s`, target: RATE },
          ],
        },
      },
};

export function setup() {
  const health = http.get(`${BASE_URL}/healthz`, { timeout: '30s' });
  if (health.status !== 200) {
    throw new Error(`app not healthy: ${health.status}`);
  }
  return {};
}

function verify(res) {
  let body;
  try {
    body = res.json();
  } catch (e) {
    return 'body not json';
  }
  const ops = body.ops || {};
  const knobs = body.knobs || {};
  if (EXPECT.jdbc !== null && ops.jdbc !== EXPECT.jdbc) return `ops.jdbc=${ops.jdbc}`;
  if (EXPECT.redis !== null && ops.redis !== EXPECT.redis) return `ops.redis=${ops.redis}`;
  if (EXPECT.http !== null && ops.http !== EXPECT.http) return `ops.http=${ops.http}`;
  if (EXPECT.units !== null && knobs.cpu_units !== EXPECT.units) return `knobs.cpu_units=${knobs.cpu_units}`;
  return null;
}

function phase() {
  if (MODE === 'rate') return 'measure';
  return (Date.now() - exec.scenario.startTime) < WARM_S * 1000 ? 'warm' : 'measure';
}

export default function () {
  const ph = phase();
  const res = http.post(`${BASE_URL}/api/checkout`, null, {
    tags: { scenario: 'checkout', phase: ph },
    timeout: '60s',
  });
  if (ph === 'warm') {
    warmRequests.add(1);
    return;
  }
  requests.add(1);
  latency.add(res.timings.duration);
  if (res.status !== 200) {
    errors.add(1);
    return;
  }
  const why = verify(res);
  if (why !== null) {
    checkFail.add(1);
    if (__ITER < 3) console.error(`response check failed: ${why}`);
  }
}

export function handleSummary(data) {
  const m = data.metrics;
  const count = (name) => (m[name] ? m[name].values.count : 0);
  const lat = m.lat_checkout;
  const reqs = count('req_checkout');
  const errs = count('err_checkout');
  const checkout = {
    count: reqs,
    errors: errs,
    avg_ms: lat ? lat.values.avg : null,
    med_ms: lat ? lat.values.med : null,
    p95_ms: lat ? lat.values['p(95)'] : null,
    p99_ms: lat ? lat.values['p(99)'] : null,
    max_ms: lat ? lat.values.max : null,
  };
  const out = {
    mode: MODE,
    duration: DURATION,
    warm_s: MODE === 'rate' ? 0 : WARM_S,
    warm_ramp: MODE === 'rate' ? null : WARM_RAMP,
    rate: String(RATE),
    pre_vus: PRE_VUS,
    max_vus: MAX_VUS,
    expect: EXPECT,
    scenarios: { checkout },
    overall: {
      requests: reqs,
      warm_requests: count('req_warm'),
      all_requests: count('http_reqs'),
      test_duration_s: DURATION_S,
      total_run_s: (data.state && data.state.testRunDurationMs) ? data.state.testRunDurationMs / 1000 : null,
      tps: DURATION_S ? reqs / DURATION_S : null,
      avg_ms: checkout.avg_ms,
      med_ms: checkout.med_ms,
      p95_ms: checkout.p95_ms,
      p99_ms: checkout.p99_ms,
      max_ms: checkout.max_ms,
      failed_rate: reqs ? errs / reqs : null,
      dropped_iterations: count('dropped_iterations'),
      check_fail: count('check_fail'),
      vus_max: m.vus_max ? m.vus_max.values.value : null,
    },
  };

  // Marker-delimited so the orchestrator can lift the summary out of whatever
  // else k6 wrote to the job's stdout.
  return { 'stdout': `@@SUMMARY_BEGIN@@\n${JSON.stringify(out)}\n@@SUMMARY_END@@\n` };
}
