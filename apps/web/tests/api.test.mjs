import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import {
  ApiError,
  DEFAULT_MUTATION_REQUEST_TIMEOUT_MS,
  DEFAULT_REQUEST_TIMEOUT_MS,
  request,
  sectorFlowAuditStatus,
  sectorLeaderChangeAuditStatus,
  sectorModelExpectedExcess,
} from '../src/api.ts'

const originalFetch = globalThis.fetch
const originalSetTimeout = globalThis.setTimeout

function abortablePendingFetch(onSignal) {
  return (_input, init = {}) => {
    const signal = init.signal
    assert.ok(signal instanceof AbortSignal, 'fetch should receive the internal abort signal')
    onSignal?.(signal)
    return new Promise((_resolve, reject) => {
      signal.addEventListener(
        'abort',
        () => reject(new DOMException('Aborted', 'AbortError')),
        { once: true },
      )
    })
  }
}

afterEach(() => {
  globalThis.fetch = originalFetch
  globalThis.setTimeout = originalSetTimeout
})

test('GET timeout aborts fetch and returns a displayable timeout error', async () => {
  let fetchSignal
  globalThis.fetch = abortablePendingFetch((signal) => {
    fetchSignal = signal
  })

  await assert.rejects(
    request('/slow', { timeoutMs: 10 }),
    (error) => {
      assert.ok(error instanceof ApiError)
      assert.equal(error.status, 0)
      assert.equal(error.kind, 'timeout')
      assert.match(error.message, /请求超时/)
      return true
    },
  )
  assert.equal(fetchSignal.aborted, true)
})

test('read and mutation requests have separate safe default deadlines', async () => {
  assert.equal(DEFAULT_REQUEST_TIMEOUT_MS, 15_000)
  assert.equal(DEFAULT_MUTATION_REQUEST_TIMEOUT_MS, 120_000)

  globalThis.fetch = async () => new Response('{}')
  let scheduledDelay
  globalThis.setTimeout = (callback, delay, ...args) => {
    scheduledDelay = delay
    return originalSetTimeout(callback, 1_000_000, ...args)
  }

  const read = request('/read')
  globalThis.setTimeout = originalSetTimeout
  await read
  assert.equal(scheduledDelay, DEFAULT_REQUEST_TIMEOUT_MS)

  globalThis.setTimeout = (callback, delay, ...args) => {
    scheduledDelay = delay
    return originalSetTimeout(callback, 1_000_000, ...args)
  }
  const mutation = request('/mutation', { method: 'POST' })
  globalThis.setTimeout = originalSetTimeout
  await mutation
  assert.equal(scheduledDelay, DEFAULT_MUTATION_REQUEST_TIMEOUT_MS)
})

test('mutation timeout warns that the server-side result may already exist', async () => {
  globalThis.fetch = abortablePendingFetch()

  await assert.rejects(
    request('/mutation', { method: 'POST', timeoutMs: 10 }),
    (error) => {
      assert.ok(error instanceof ApiError)
      assert.equal(error.kind, 'timeout')
      assert.match(error.message, /结果可能已提交/)
      assert.match(error.message, /勿直接重试/)
      return true
    },
  )
})

test('caller cancellation aborts the same fetch without being reported as timeout', async () => {
  const caller = new AbortController()
  let fetchSignal
  globalThis.fetch = abortablePendingFetch((signal) => {
    fetchSignal = signal
  })

  const pending = request('/cancelled', {
    signal: caller.signal,
    timeoutMs: 60_000,
  })
  assert.notEqual(fetchSignal, caller.signal)
  assert.equal(fetchSignal.aborted, false)

  caller.abort()

  await assert.rejects(
    pending,
    (error) => {
      assert.ok(error instanceof ApiError)
      assert.equal(error.status, 0)
      assert.equal(error.kind, 'cancelled')
      assert.equal(error.message, '请求已取消。')
      return true
    },
  )
  assert.equal(fetchSignal.aborted, true)
})

test('first abort wins when fetch rejection arrives after the timeout deadline', async () => {
  const caller = new AbortController()
  let rejectFetch
  globalThis.fetch = () => new Promise((_resolve, reject) => {
    rejectFetch = reject
  })

  const pending = request('/slow-to-cancel', {
    signal: caller.signal,
    timeoutMs: 10,
  })
  caller.abort()
  await new Promise((resolve) => setTimeout(resolve, 20))
  rejectFetch(new DOMException('Aborted', 'AbortError'))

  await assert.rejects(
    pending,
    (error) => error instanceof ApiError && error.kind === 'cancelled',
  )
})

test('already-cancelled caller signal prevents fetch from starting', async () => {
  const caller = new AbortController()
  caller.abort()
  let fetchCalls = 0
  globalThis.fetch = () => {
    fetchCalls += 1
    return Promise.reject(new Error('unexpected fetch'))
  }

  await assert.rejects(
    request('/cancelled-before-start', { signal: caller.signal }),
    (error) => error instanceof ApiError && error.kind === 'cancelled',
  )
  assert.equal(fetchCalls, 0)
})

test('successful requests detach caller listener and clear the timeout', async () => {
  const caller = new AbortController()
  let fetchSignal
  globalThis.fetch = async (_input, init = {}) => {
    fetchSignal = init.signal
    return new Response(JSON.stringify({ ok: true }), {
      headers: { 'Content-Type': 'application/json' },
    })
  }

  assert.deepEqual(
    await request('/fast', { signal: caller.signal, timeoutMs: 10 }),
    { ok: true },
  )
  caller.abort()
  await new Promise((resolve) => setTimeout(resolve, 20))

  assert.equal(fetchSignal.aborted, false)
})

function sectorResponse(overrides = {}) {
  return {
    as_of: '2026-08-07',
    horizon: 5,
    model_version: 'sector-test',
    flow_mode: 'full',
    backtest_scope: 'fixed-current-membership',
    degraded_reason: null,
    available: true,
    count: 1,
    rows: [],
    flow_as_of: '2026-08-07',
    flow_window_days: 5,
    strength_as_of: '2026-07-23T08:00:00Z',
    input_trade_date: '2026-08-07',
    input_coverage: null,
    ignored_forecast_dates: [],
    stale: false,
    warning: null,
    ...overrides,
  }
}

function sectorRow(overrides = {}) {
  return {
    rank: 1,
    plate_code: 'SH.LIST0044',
    plate_name: '贵金属',
    trade_date: '2026-08-07',
    horizon: 5,
    score: 100,
    expected_excess: 0.0022,
    win_rate: 0.495,
    lifecycle: null,
    rsi14: null,
    reversal_score: null,
    model_version: 'sector-test',
    net_inflow: null,
    net_inflow_5d: null,
    flow_coverage_days: 1,
    flow_source: null,
    leader_code: '600547',
    leader_name: '山东黄金',
    leader_change_pct: 10,
    ...overrides,
  }
}

test('sector flow audit names the missing forecast day and preserves x/5 coverage', () => {
  const response = sectorResponse({
    flow_window_dates: [
      '2026-08-03',
      '2026-08-04',
      '2026-08-05',
      '2026-08-06',
      '2026-08-07',
    ],
  })
  const row = sectorRow({
    flow_trade_date: null,
    flow_available_dates: ['2026-08-05'],
    flow_missing_dates: ['2026-08-03', '2026-08-04', '2026-08-06', '2026-08-07'],
  })

  assert.deepEqual(sectorFlowAuditStatus(row, response), {
    availableForForecast: false,
    tradeDate: null,
    expectedDate: '2026-08-07',
    availableDates: ['2026-08-05'],
    missingDates: ['2026-08-03', '2026-08-04', '2026-08-06', '2026-08-07'],
    coverageDays: 1,
    windowDays: 5,
  })
})

test('legacy sector flow is accepted only when its response date matches the forecast day', () => {
  const row = sectorRow({ net_inflow: 12_000 })
  assert.equal(sectorFlowAuditStatus(row, sectorResponse()).availableForForecast, true)
  assert.equal(
    sectorFlowAuditStatus(row, sectorResponse({ flow_as_of: '2026-08-06' })).availableForForecast,
    false,
  )
})

test('leader return is hidden when stale, undated, or not audited daily bars', () => {
  const response = sectorResponse({
    leader_as_of: '2026-07-23',
    leader_source: 'daily_bars',
  })
  const stale = sectorLeaderChangeAuditStatus(
    sectorRow({ leader_as_of: '2026-07-23', leader_source: 'daily_bars' }),
    response,
  )
  assert.equal(stale.available, false)
  assert.equal(stale.value, null)
  assert.equal(stale.reason, 'date-mismatch')

  const undated = sectorLeaderChangeAuditStatus(sectorRow(), sectorResponse())
  assert.equal(undated.reason, 'missing-date')
  assert.equal(undated.value, null)

  const unverified = sectorLeaderChangeAuditStatus(
    sectorRow({ leader_as_of: '2026-08-07' }),
    sectorResponse({ leader_as_of: '2026-08-07' }),
  )
  assert.equal(unverified.reason, 'unverified-source')
  assert.equal(unverified.value, null)

  const explicitlyUnavailable = sectorLeaderChangeAuditStatus(
    sectorRow({ leader_as_of: null, leader_source: null }),
    sectorResponse({ leader_as_of: '2026-08-07', leader_source: 'daily_bars' }),
  )
  assert.equal(explicitlyUnavailable.reason, 'missing-date')
  assert.equal(explicitlyUnavailable.value, null)
})

test('leader return is exposed only for forecast-day audited daily bars', () => {
  const status = sectorLeaderChangeAuditStatus(
    sectorRow({
      leader_as_of: '2026-08-07',
      leader_previous_trade_date: '2026-08-06',
      leader_source: 'daily_bars',
      leader_change_pct: -4.43,
    }),
    sectorResponse({ leader_as_of: '2026-08-07', leader_source: 'daily_bars' }),
  )
  assert.deepEqual(status, {
    available: true,
    value: -4.43,
    asOf: '2026-08-07',
    previousTradeDate: '2026-08-06',
    reason: 'available',
  })
})

test('model expected excess prefers the panel metric and only uses a uniform legacy fallback', () => {
  const rows = [sectorRow(), sectorRow({ plate_code: 'SH.LIST0068' })]
  assert.equal(
    sectorModelExpectedExcess(sectorResponse({ rows, model_expected_excess: 0.0123 })),
    0.0123,
  )
  assert.equal(sectorModelExpectedExcess(sectorResponse({ rows })), 0.0022)
  assert.equal(
    sectorModelExpectedExcess(
      sectorResponse({ rows: [rows[0], sectorRow({ expected_excess: 0.009 })] }),
    ),
    null,
  )
})
