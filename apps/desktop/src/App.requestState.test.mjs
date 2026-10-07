import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'
import vm from 'node:vm'

const require = createRequire(import.meta.url)
const ts = require('typescript')
const appSource = readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
const script = appSource.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)?.[1]
assert.ok(script, 'App.vue script setup was not found')

const names = [
  'rememberRequestDiagnostics', 'requestDiagnosticDetail', 'updateResumePhase',
  'clearClassificationWatchdog', 'clearCancelWatchdog', 'settleCancellation',
  'clearModelProbeTimeout', 'failModelProbe', 'workerErrorDetail', 'handleWorkerMessage',
]
const appFile = ts.createSourceFile('App.vue.ts', script, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const declarations = new Map(
  appFile.statements
    .filter((node) => ts.isFunctionDeclaration(node) && node.name)
    .map((node) => [node.name.text, node.getText(appFile)]),
)
const actualFunctions = names.map((name) => {
  const source = declarations.get(name)
  assert.ok(source, `App.vue function ${name} was not found`)
  return source
}).join('\n')

const refNames = [
  'workerConnected', 'workerReadyState', 'workerInterrupted', 'status', 'taskPhase', 'taskDetail', 'resumeSnapshot',
  'classificationRunning', 'classificationRequestId', 'generationRequestId', 'isRunning', 'isCancelling',
  'cancelRequestId', 'cancelTargetRequestId', 'modelTesting', 'modelProbeRequestId', 'modelStatus', 'inputRoot', 'outputRoot',
]
const fixtureSource = `
function createFixture(initial) {
  const refs = Object.fromEntries(__refNames.map((name) => [name, { value: initial[name] }]))
  let classificationWatchdog = initial.classificationWatchdog
  let cancelWatchdog = initial.cancelWatchdog
  let modelProbeTimeout = initial.modelProbeTimeout
  let modelProbeRoot = initial.modelProbeRoot || ''
  let modelProbeReadyHandled = Boolean(initial.modelProbeReadyHandled)
  const clearedTimers = []
  const classificationProgress = []
  const releasedModelProbeRoots = []
  const requestDiagnostics = new Map(Object.entries(initial.requestDiagnostics || {}))
  const window = { clearTimeout: (timer) => clearedTimers.push(timer) }
  const worker = {
    cancelled: [],
    pending: new Set(initial.pendingRequestIds || []),
    cancelRequest(requestId) {
      this.cancelled.push(requestId)
      this.pending.delete(requestId)
    },
  }
  const {
    workerConnected, workerReadyState, workerInterrupted, status, taskPhase, taskDetail, resumeSnapshot,
    classificationRunning, classificationRequestId, generationRequestId, isRunning, isCancelling,
    cancelRequestId, cancelTargetRequestId, modelTesting, modelProbeRequestId, modelStatus, inputRoot, outputRoot,
  } = refs
  function setClassificationProgress(...args) { classificationProgress.push(args) }
  function releaseModelProbe(root) { releasedModelProbeRoots.push(root) }
  function saveResumeTask(snapshot) { resumeSnapshot.value = snapshot }

  ${actualFunctions}

  globalThis.__fixture = {
    refs,
    worker,
    clearedTimers,
    classificationProgress,
    releasedModelProbeRoots,
    handleWorkerMessage,
    settleCancellation,
    get classificationWatchdog() { return classificationWatchdog },
    get cancelWatchdog() { return cancelWatchdog },
    get modelProbeTimeout() { return modelProbeTimeout },
    get modelProbeRoot() { return modelProbeRoot },
    get modelProbeReadyHandled() { return modelProbeReadyHandled },
  }
}
createFixture(__initial)
`
const transpiledFixture = ts.transpileModule(fixtureSource, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
  reportDiagnostics: true,
})
const compileErrors = (transpiledFixture.diagnostics || []).filter((item) => item.category === ts.DiagnosticCategory.Error)
assert.deepEqual(compileErrors.map((item) => ts.flattenDiagnosticMessageText(item.messageText, '\n')), [])

function createFixture(initial = {}) {
  const context = vm.createContext({ __initial: initial, __refNames: refNames })
  vm.runInContext(transpiledFixture.outputText, context, { timeout: 1_000 })
  return context.__fixture
}

const plain = (value) => JSON.parse(JSON.stringify(value))
const ids = (values) => Array.from(values)
const globalFault = (code, message) => ({
  protocol: 1,
  request_id: 'worker',
  type: 'error',
  payload: { code, message },
})

test('an idle global fault only changes Worker connection state', () => {
  const previousResume = { phase: 'completed', task_id: 'main-old', task_detail: 'finished' }
  const fixture = createFixture({
    workerConnected: true,
    workerReadyState: '已连接',
    status: '生成完成',
    taskPhase: '已完成',
    taskDetail: '所有任务已完成。',
    resumeSnapshot: previousResume,
  })

  fixture.handleWorkerMessage(globalFault('transport_error', 'pipe closed'))

  assert.equal(fixture.refs.workerConnected.value, false)
  assert.equal(fixture.refs.workerReadyState.value, 'Worker 通信异常')
  assert.equal(fixture.refs.status.value, '生成完成')
  assert.equal(fixture.refs.taskPhase.value, '已完成')
  assert.equal(fixture.refs.taskDetail.value, '所有任务已完成。')
  assert.deepEqual(plain(fixture.refs.resumeSnapshot.value), previousResume)
  assert.equal(fixture.refs.workerInterrupted.value, undefined)
})

test('a model-only global fault ends the probe without changing the main task', () => {
  const previousResume = { phase: 'completed', task_id: 'main-old' }
  const fixture = createFixture({
    status: '准备就绪',
    taskPhase: '已完成',
    taskDetail: 'main task stayed complete',
    resumeSnapshot: previousResume,
    modelTesting: true,
    modelProbeRequestId: 'settings-probe-9',
    modelProbeRoot: 'C:\\models',
    modelProbeTimeout: 321,
  })

  fixture.handleWorkerMessage(globalFault('transport_error', 'Worker pipe closed'))

  assert.equal(fixture.refs.modelTesting.value, false)
  assert.equal(fixture.refs.modelProbeRequestId.value, undefined)
  assert.equal(fixture.modelProbeRoot, '')
  assert.equal(fixture.modelProbeTimeout, undefined)
  assert.deepEqual(ids(fixture.worker.cancelled), ['settings-probe-9'])
  assert.deepEqual(ids(fixture.clearedTimers), [321])
  assert.deepEqual(ids(fixture.releasedModelProbeRoots), ['C:\\models'])
  assert.match(fixture.refs.modelStatus.value, /Worker 已中断/)
  assert.equal(fixture.refs.status.value, '准备就绪')
  assert.equal(fixture.refs.taskPhase.value, '已完成')
  assert.equal(fixture.refs.taskDetail.value, 'main task stayed complete')
  assert.deepEqual(plain(fixture.refs.resumeSnapshot.value), previousResume)
})

test('global faults interrupt active classification and generation while preserving resume identity', () => {
  for (const kind of ['classification', 'generation']) {
    const requestId = kind === 'classification' ? 'classify-17' : 'main-generate-23'
    const stage = kind === 'classification' ? 'path_scan' : 'emit'
    const fixture = createFixture({
      status: '正在执行',
      taskPhase: kind === 'classification' ? '识别分类' : '生成图片',
      taskDetail: 'working before fault',
      workerConnected: true,
      classificationRunning: kind === 'classification',
      classificationRequestId: kind === 'classification' ? requestId : undefined,
      generationRequestId: kind === 'generation' ? requestId : undefined,
      isRunning: kind === 'generation',
      isCancelling: true,
      cancelRequestId: 'cancel-ack-4',
      cancelTargetRequestId: requestId,
      classificationWatchdog: 101,
      cancelWatchdog: 202,
      resumeSnapshot: { phase: kind === 'classification' ? 'classifying' : 'generating', task_id: requestId },
      requestDiagnostics: { [requestId]: { stage, logPath: 'worker.log' } },
    })

    fixture.handleWorkerMessage(globalFault('worker_exit', 'reader exited'))

    assert.equal(fixture.refs.workerConnected.value, false)
    assert.equal(fixture.refs.workerInterrupted.value, true)
    assert.equal(fixture.refs.classificationRunning.value, false)
    assert.equal(fixture.refs.isRunning.value, false)
    assert.equal(fixture.refs.isCancelling.value, false)
    assert.equal(fixture.refs.classificationRequestId.value, undefined)
    assert.equal(fixture.refs.generationRequestId.value, undefined)
    assert.equal(fixture.refs.cancelRequestId.value, undefined)
    assert.equal(fixture.refs.cancelTargetRequestId.value, undefined)
    assert.equal(fixture.classificationWatchdog, undefined)
    assert.equal(fixture.cancelWatchdog, undefined)
    assert.deepEqual(ids(fixture.clearedTimers), [101, 202])
    assert.equal(fixture.refs.taskPhase.value, `${kind === 'classification' ? '识别分类' : '生成图片'}中断`)
    assert.match(fixture.refs.taskDetail.value, new RegExp(`${stage}.*worker\\.log`))
    assert.equal(fixture.refs.resumeSnapshot.value.phase, 'interrupted')
    assert.equal(fixture.refs.resumeSnapshot.value.task_id, requestId)
    assert.deepEqual(plain(fixture.classificationProgress), kind === 'classification' ? [[0, true]] : [])
  }
})

test('a repeated global exit does not replace the first interruption detail', () => {
  const requestId = 'main-generate-first'
  const fixture = createFixture({
    status: '正在生成',
    taskPhase: '生成图片',
    taskDetail: 'before fault',
    generationRequestId: requestId,
    isRunning: true,
    resumeSnapshot: { phase: 'generating', task_id: requestId },
  })

  fixture.handleWorkerMessage(globalFault('worker_exit', 'first exit detail'))
  const first = {
    status: fixture.refs.status.value,
    taskPhase: fixture.refs.taskPhase.value,
    taskDetail: fixture.refs.taskDetail.value,
    resume: plain(fixture.refs.resumeSnapshot.value),
  }
  fixture.handleWorkerMessage(globalFault('transport_error', 'later duplicate detail'))

  assert.equal(fixture.refs.status.value, first.status)
  assert.equal(fixture.refs.taskPhase.value, first.taskPhase)
  assert.equal(fixture.refs.taskDetail.value, first.taskDetail)
  assert.deepEqual(plain(fixture.refs.resumeSnapshot.value), first.resume)
  assert.match(fixture.refs.taskDetail.value, /first exit detail/)
  assert.doesNotMatch(fixture.refs.taskDetail.value, /later duplicate detail/)
})

test('settleCancellation clears target and acknowledgement requests and ignores late events', () => {
  const targetId = 'classify-cancel-target'
  const ackId = 'cancel-ack-12'
  const fixture = createFixture({
    status: '正在取消',
    taskPhase: '正在安全停止',
    taskDetail: 'stopping',
    classificationRunning: true,
    classificationRequestId: targetId,
    isCancelling: true,
    cancelTargetRequestId: targetId,
    cancelRequestId: ackId,
    classificationWatchdog: 301,
    cancelWatchdog: 302,
    resumeSnapshot: { phase: 'classifying', task_id: targetId },
    pendingRequestIds: [targetId, ackId],
  })

  fixture.settleCancellation('取消请求已完成', '当前任务已结束。')
  const settled = {
    status: fixture.refs.status.value,
    taskPhase: fixture.refs.taskPhase.value,
    taskDetail: fixture.refs.taskDetail.value,
    resume: plain(fixture.refs.resumeSnapshot.value),
  }

  assert.deepEqual(ids(fixture.worker.cancelled), [targetId, ackId])
  assert.equal(fixture.worker.pending.size, 0)
  assert.equal(fixture.refs.classificationRunning.value, false)
  assert.equal(fixture.refs.classificationRequestId.value, undefined)
  assert.equal(fixture.refs.cancelTargetRequestId.value, undefined)
  assert.equal(fixture.refs.cancelRequestId.value, undefined)
  assert.equal(fixture.refs.isCancelling.value, false)
  assert.equal(fixture.classificationWatchdog, undefined)
  assert.equal(fixture.cancelWatchdog, undefined)
  assert.deepEqual(ids(fixture.clearedTimers), [302, 301])
  assert.equal(fixture.refs.resumeSnapshot.value.phase, 'cancelled')

  fixture.handleWorkerMessage({ protocol: 1, request_id: targetId, type: 'cancelled', payload: {} })
  fixture.handleWorkerMessage({ protocol: 1, request_id: ackId, type: 'status', payload: { state: 'idle' } })
  fixture.handleWorkerMessage({ protocol: 1, request_id: ackId, type: 'error', payload: { message: 'late ack error' } })

  assert.equal(fixture.refs.status.value, settled.status)
  assert.equal(fixture.refs.taskPhase.value, settled.taskPhase)
  assert.equal(fixture.refs.taskDetail.value, settled.taskDetail)
  assert.deepEqual(plain(fixture.refs.resumeSnapshot.value), settled.resume)
})

test('a Worker-originated classification cancelled event settles without a UI cancel target', () => {
  const requestId = 'classify-worker-cancelled'
  const fixture = createFixture({
    status: '正在分析素材',
    taskPhase: '识别分类',
    taskDetail: 'working',
    classificationRunning: true,
    classificationRequestId: requestId,
    classificationWatchdog: 401,
    resumeSnapshot: { phase: 'classifying', task_id: requestId },
    pendingRequestIds: [requestId],
  })

  fixture.handleWorkerMessage({ protocol: 1, request_id: requestId, type: 'cancelled', payload: { message: 'cancelled' } })

  assert.equal(fixture.refs.classificationRunning.value, false)
  assert.equal(fixture.refs.classificationRequestId.value, undefined)
  assert.equal(fixture.refs.cancelTargetRequestId.value, undefined)
  assert.equal(fixture.refs.isCancelling.value, false)
  assert.equal(fixture.classificationWatchdog, undefined)
  assert.deepEqual(ids(fixture.clearedTimers), [401])
  assert.deepEqual(ids(fixture.worker.cancelled), [requestId])
  assert.equal(fixture.worker.pending.size, 0)
  assert.equal(fixture.refs.resumeSnapshot.value.phase, 'cancelled')
  assert.equal(fixture.refs.resumeSnapshot.value.task_id, requestId)
  assert.equal(fixture.refs.taskPhase.value, '已安全停止')
  assert.equal(fixture.refs.status.value, '素材分析已取消')
})

test('cancel acknowledgement errors restore the active phase and keep task requests running', () => {
  for (const kind of ['classification', 'generation']) {
    const requestId = kind === 'classification' ? 'classify-cancel-error' : 'main-generate-cancel-error'
    const ackId = `cancel-ack-${kind}`
    const reason = '取消控制通道不可用'
    const fixture = createFixture({
      status: '正在取消',
      taskPhase: '正在安全停止',
      taskDetail: 'Worker 正在停止当前任务。',
      classificationRunning: kind === 'classification',
      classificationRequestId: kind === 'classification' ? requestId : undefined,
      isRunning: kind === 'generation',
      generationRequestId: kind === 'generation' ? requestId : undefined,
      isCancelling: true,
      cancelRequestId: ackId,
      cancelTargetRequestId: requestId,
      classificationWatchdog: 601,
      cancelWatchdog: 602,
      pendingRequestIds: [requestId, ackId],
    })

    fixture.handleWorkerMessage({
      protocol: 1,
      request_id: ackId,
      type: 'error',
      payload: { code: 'send_failed', message: reason },
    })

    assert.equal(fixture.refs.status.value, '取消请求失败')
    assert.equal(fixture.refs.taskPhase.value, kind === 'classification' ? '识别分类' : '生成图片')
    assert.match(fixture.refs.taskDetail.value, new RegExp(reason))
    assert.match(fixture.refs.taskDetail.value, /当前任务仍在运行/)
    assert.equal(fixture.refs.classificationRunning.value, kind === 'classification')
    assert.equal(fixture.refs.isRunning.value, kind === 'generation')
    assert.equal(fixture.refs.classificationRequestId.value, kind === 'classification' ? requestId : undefined)
    assert.equal(fixture.refs.generationRequestId.value, kind === 'generation' ? requestId : undefined)
    assert.equal(fixture.refs.isCancelling.value, false)
    assert.equal(fixture.refs.cancelRequestId.value, undefined)
    assert.equal(fixture.refs.cancelTargetRequestId.value, undefined)
    assert.equal(fixture.classificationWatchdog, 601)
    assert.equal(fixture.cancelWatchdog, undefined)
    assert.deepEqual(ids(fixture.clearedTimers), [602])
    assert.deepEqual(ids(fixture.worker.cancelled), [ackId])
    assert.equal(fixture.worker.pending.size, 1)
    assert.equal(fixture.worker.pending.has(requestId), true)
    assert.equal(fixture.worker.pending.has(ackId), false)
  }
})
