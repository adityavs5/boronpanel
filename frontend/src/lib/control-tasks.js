import { get, post } from './api'

const TERMINAL = new Set(['completed', 'failed', 'cancelled'])

function pause(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

export async function waitForControlTask(initial, { onProgress, reveal = false } = {}) {
  let task = initial
  const deadline = Date.now() + 10 * 60 * 1000
  while (!TERMINAL.has(task.status)) {
    if (Date.now() >= deadline) throw new Error(`The operation is still running. Track task ${task.id} on the Tasks page.`)
    onProgress?.(task)
    await pause(900)
    task = await get(`/api/v1/task-operations/${task.id}`)
  }
  onProgress?.(task)
  if (task.status !== 'completed') {
    // progress_message carries the handler's own reason for user errors
    // ("account 'x' already exists", a busy lock); internal failures only
    // say "Operation failed" and are traced through error_reference.
    const reason = task.progress_message && task.progress_message !== 'Operation failed' ? task.progress_message : 'The operation failed.'
    const reference = task.error_reference || null
    throw Object.assign(new Error(reference ? `${reason} (reference: ${reference})` : reason), { reason, reference, task })
  }
  if (reveal) return post(`/api/v1/task-operations/${task.id}/reveal`, {})
  return task.result || {}
}
