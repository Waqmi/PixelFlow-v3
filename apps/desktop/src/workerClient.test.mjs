import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import test from 'node:test'

const modulePath = process.env.PIXELFLOW_WORKER_CLIENT_MODULE
if (!modulePath) throw new Error('Set PIXELFLOW_WORKER_CLIENT_MODULE to the temporary CommonJS build of workerClient.ts')
const { WorkerClient } = createRequire(import.meta.url)(modulePath)

const eventLine = (request_id, type, payload = {}) =>
  JSON.stringify({ protocol: 1, request_id, type, payload })

class FakeBridge {
  listeners = new Set()
  sent = []
  sendError

  send(line) {
    const request = JSON.parse(line)
    this.sent.push(request)
    if (this.sendError) throw this.sendError
  }

  subscribe(listener) {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  emit(request_id, type, payload = {}) {
    this.emitRaw(eventLine(request_id, type, payload))
  }

  emitRaw(line) {
    for (const listener of [...this.listeners]) listener(line)
  }

  lastRequestId() {
    return this.sent.at(-1).request_id
  }
}

const makeClient = (bridge, prefix = 'test') => new WorkerClient(bridge, undefined, prefix, 250)

test('requestResult resolves once and ignores status after settlement or from unknown IDs', async () => {
  const bridge = new FakeBridge()
  const client = makeClient(bridge)
  const observed = []
  let requestId
  client.onMessage((message) => {
    observed.push(message)
    if (message.request_id === requestId && message.type === 'done') {
      bridge.emit(requestId, 'status', { state: 'reentrant-late' })
    }
  })

  const result = client.requestResult('probe_models', { model_path: '模型' })
  requestId = bridge.lastRequestId()
  const payload = { models: ['model-a'] }
  bridge.emit(requestId, 'done', payload)

  assert.deepEqual(await result, payload)
  bridge.emit(requestId, 'status', { state: 'late' })
  bridge.emit('other-client-1', 'status', { state: 'unknown' })
  assert.deepEqual(observed.map(({ request_id, type }) => [request_id, type]), [[requestId, 'done']])
  client.dispose()
})

test('a valid request error rejects only its own request', async () => {
  const bridge = new FakeBridge()
  const client = makeClient(bridge)
  const failed = client.requestResult('probe_models', {})
  const failedId = bridge.lastRequestId()
  const failedAssertion = assert.rejects(failed, /model scan failed/)
  const pending = client.requestResult('read_preview', {})
  const pendingId = bridge.lastRequestId()

  bridge.emit(failedId, 'error', { message: 'model scan failed' })
  await failedAssertion
  const payload = { data: 'preview' }
  bridge.emit(pendingId, 'done', payload)
  assert.deepEqual(await pending, payload)
  client.dispose()
})

test('malformed JSON and invalid protocol shapes fail every pending request once', async () => {
  for (const malformed of ['{', '{}', 'null']) {
    const bridge = new FakeBridge()
    const client = makeClient(bridge)
    const observed = []
    client.onMessage((message) => observed.push(message))
    const first = client.requestResult('probe_models', {})
    const firstId = bridge.lastRequestId()
    const second = client.requestResult('read_preview', {})
    const secondId = bridge.lastRequestId()
    const failures = [assert.rejects(first), assert.rejects(second)]

    bridge.emitRaw(malformed)
    await Promise.all(failures)

    assert.equal(observed.filter((message) => message.request_id === 'worker' && message.type === 'error').length, 1)
    for (const requestId of [firstId, secondId]) {
      const errors = observed.filter((message) => message.request_id === requestId && message.type === 'error')
      assert.equal(errors.length, 1)
      assert.equal(errors[0].payload.code, 'transport_error')
    }
    client.dispose()
  }
})

test('global transport failure snapshots pending requests before callbacks can start another', async () => {
  const bridge = new FakeBridge()
  const client = makeClient(bridge)
  const observed = []
  let reentrantResult
  client.onMessage((message) => {
    observed.push(message)
    if (message.request_id === 'worker' && !reentrantResult) {
      reentrantResult = client.requestResult('ping', {})
    }
  })

  const first = client.requestResult('probe_models', {})
  const firstId = bridge.lastRequestId()
  const second = client.requestResult('read_preview', {})
  const secondId = bridge.lastRequestId()
  const failures = [assert.rejects(first), assert.rejects(second)]

  bridge.emit('worker', 'error', { code: 'transport_error', message: 'reader failed' })
  await Promise.all(failures)

  assert.deepEqual(
    observed.slice(0, 3).map(({ request_id, type }) => [request_id, type]),
    [['worker', 'error'], [firstId, 'error'], [secondId, 'error']],
  )
  assert.equal(observed.filter((message) => message.request_id === firstId && message.type === 'error').length, 1)
  assert.equal(observed.filter((message) => message.request_id === secondId && message.type === 'error').length, 1)

  const reentrantId = bridge.lastRequestId()
  const payload = { ok: true }
  bridge.emit(reentrantId, 'done', payload)
  assert.deepEqual(await reentrantResult, payload)
  client.dispose()
})

test('worker exit sentinel settles all current requests and preserves its code', async () => {
  const bridge = new FakeBridge()
  const client = makeClient(bridge)
  const observed = []
  client.onMessage((message) => observed.push(message))
  const first = client.requestResult('probe_models', {})
  const firstId = bridge.lastRequestId()
  const second = client.requestResult('read_preview', {})
  const secondId = bridge.lastRequestId()
  const failures = [assert.rejects(first), assert.rejects(second)]

  bridge.emit('worker', 'error', { code: 'worker_exit', message: 'Worker exited' })
  await Promise.all(failures)
  for (const requestId of [firstId, secondId]) {
    const errors = observed.filter((message) => message.request_id === requestId && message.type === 'error')
    assert.equal(errors.length, 1)
    assert.equal(errors[0].payload.code, 'worker_exit')
  }
  client.dispose()
})

test('send failure rejects and removes its request', async () => {
  const bridge = new FakeBridge()
  const client = makeClient(bridge)
  bridge.sendError = new Error('IPC unavailable')
  const failed = client.requestResult('ping', {}, 5_000)
  const failedId = bridge.lastRequestId()
  await assert.rejects(failed, /IPC unavailable/)

  const observed = []
  client.onMessage((message) => observed.push(message))
  bridge.emit(failedId, 'done', {})
  assert.deepEqual(observed, [])
  client.dispose()
})

test('requestResult timeout settles and ignores a late response', async () => {
  const bridge = new FakeBridge()
  const client = makeClient(bridge)
  const observed = []
  client.onMessage((message) => observed.push(message))
  const timedOut = client.requestResult('probe_models', {}, 15)
  const requestId = bridge.lastRequestId()

  await assert.rejects(timedOut, /timed out/)
  assert.deepEqual(observed.map(({ request_id, type }) => [request_id, type]), [[requestId, 'error']])
  assert.equal(observed[0].payload.code, 'request_timeout')
  bridge.emit(requestId, 'done', {})
  assert.equal(observed.length, 1)
  client.dispose()
})

test('cancelRequest settles requestResult once and ignores later events', async () => {
  const bridge = new FakeBridge()
  const client = makeClient(bridge)
  const observed = []
  client.onMessage((message) => observed.push(message))
  const cancelled = client.requestResult('probe_models', {}, 5_000)
  const requestId = bridge.lastRequestId()
  const cancellation = assert.rejects(cancelled, /cancel/i)

  client.cancelRequest(requestId)
  await cancellation
  const countAfterCancel = observed.filter((message) => message.request_id === requestId).length
  client.cancelRequest(requestId)
  bridge.emit(requestId, 'done', {})
  bridge.emit(requestId, 'status', { state: 'late' })
  assert.equal(observed.filter((message) => message.request_id === requestId).length, countAfterCancel)
  client.dispose()
})

test('cancelled event and valid cancellation acknowledgement are terminal', async () => {
  const bridge = new FakeBridge()
  const client = makeClient(bridge)
  const observed = []
  client.onMessage((message) => observed.push(message))

  const cancelled = client.requestResult('start_generation', {}, 5_000)
  const cancelledId = bridge.lastRequestId()
  const cancellation = assert.rejects(cancelled, /cancel/i)
  bridge.emit(cancelledId, 'cancelled', { task_id: 'task-2', message: 'cancelled' })
  await cancellation
  bridge.emit(cancelledId, 'done', { late: true })
  assert.deepEqual(
    observed.filter((message) => message.request_id === cancelledId).map((message) => message.type),
    ['cancelled'],
  )

  const cancelId = client.request('cancel_generation', { task_id: 'task-1' })
  bridge.emit(cancelId, 'status', { state: 'cancelling' })
  bridge.emit(cancelId, 'status', { state: 'idle' })
  bridge.emit(cancelId, 'status', { state: 'late' })
  assert.deepEqual(
    observed.filter((message) => message.request_id === cancelId).map(({ type, payload }) => [type, payload.state]),
    [['status', 'cancelling']],
  )
  client.dispose()
})


test('a throwing subscriber cannot block other listeners or global failure fanout', async () => {
  const bridge = new FakeBridge()
  const client = makeClient(bridge)
  const observed = []
  client.onMessage((message) => {
    if (message.request_id === 'worker') throw new Error('subscriber failed')
  })
  client.onMessage((message) => observed.push(message))

  const first = client.requestResult('probe_models', {})
  const firstId = bridge.lastRequestId()
  const second = client.requestResult('read_preview', {})
  const secondId = bridge.lastRequestId()
  const failures = [assert.rejects(first), assert.rejects(second)]

  const originalError = console.error
  console.error = () => {}
  try {
    assert.doesNotThrow(() => {
      bridge.emit('worker', 'error', { code: 'transport_error', message: 'reader failed' })
    })
    await Promise.all(failures)
  } finally {
    console.error = originalError
  }

  assert.deepEqual(
    observed.map(({ request_id, type }) => [request_id, type]),
    [['worker', 'error'], [firstId, 'error'], [secondId, 'error']],
  )
  client.dispose()
})
