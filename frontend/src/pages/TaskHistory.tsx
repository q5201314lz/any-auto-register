import { useEffect, useState } from 'react'
import { getPlatforms } from '@/lib/app-data'
import { apiFetch } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { TaskLogPanel } from '@/components/tasks/TaskLogPanel'
import { getTaskStatusText, TASK_STATUS_VARIANTS } from '@/lib/tasks'
import { RefreshCw, Activity, CheckCircle2, AlertTriangle, Clock3, ChevronDown, Eye, Square, X } from 'lucide-react'

function shortId(id: string) {
  if (!id) return '-'
  return id.length > 12 ? '...' + id.slice(-8) : id
}

function formatError(error: string | null | undefined): string {
  if (!error) return ''
  // Try to extract a readable message from JSON-like strings
  try {
    if (error.startsWith('{') || error.startsWith('[')) {
      const parsed = JSON.parse(error)
      if (parsed.message) return parsed.message
      if (parsed.error) return parsed.error
      if (Array.isArray(parsed.errors) && parsed.errors.length > 0) {
        const first = parsed.errors[0]
        return first.message || first.kind || JSON.stringify(first).slice(0, 80)
      }
    }
  } catch {
    // not JSON
  }
  // Truncate long strings
  return error.length > 100 ? error.slice(0, 100) + '...' : error
}

export default function TaskHistory() {
  const [tasks, setTasks] = useState<any[]>([])
  const [platform, setPlatform] = useState('')
  const [status, setStatus] = useState('')
  const [platforms, setPlatforms] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedTask, setSelectedTask] = useState<any | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [cancellingId, setCancellingId] = useState('')

  const load = async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ page: '1', page_size: '50' })
      if (platform) params.set('platform', platform)
      if (status) params.set('status', status)
      const data = await apiFetch(`/tasks?${params}`)
      setTasks(data.items || [])
    } finally {
      setLoading(false)
    }
  }

  const openDetail = async (task: any) => {
    setSelectedTask(task)
    setDetailLoading(true)
    try {
      const latest = await apiFetch(`/tasks/${task.id}`)
      setSelectedTask(latest)
    } finally {
      setDetailLoading(false)
    }
  }

  const terminateTask = async (task: any) => {
    if (!task?.id || !task.cancellable) return
    setCancellingId(task.id)
    const optimistic = {
      ...task,
      status: 'cancelled',
      terminal: true,
      cancellable: false,
      error: task.error || '任务已被手动终止',
      finished_at: task.finished_at || new Date().toISOString(),
    }
    setTasks(prev => prev.map(item => item.id === task.id ? optimistic : item))
    if (selectedTask?.id === task.id) setSelectedTask(optimistic)
    try {
      const latest = await apiFetch(`/tasks/${task.id}/cancel`, { method: 'POST' })
      setTasks(prev => prev.map(item => item.id === task.id ? latest : item))
      if (selectedTask?.id === task.id) setSelectedTask(latest)
    } catch (error: any) {
      window.alert(error?.message || '终止任务失败')
      load().catch(() => {})
    } finally {
      setCancellingId('')
    }
  }

  useEffect(() => {
    getPlatforms()
      .then((data) => setPlatforms(data || []))
      .catch(() => setPlatforms([]))
  }, [])

  useEffect(() => {
    load()
  }, [platform, status])

  const succeeded = tasks.filter((t) => t.status === 'succeeded').length
  const failed = tasks.filter((t) => t.status === 'failed').length
  const running = tasks.filter((t) =>
    ['running', 'claimed', 'pending', 'cancel_requested'].includes(t.status)
  ).length

  const metricCards = [
    { label: '任务数', value: tasks.length, icon: Activity, tone: 'text-[var(--accent)]' },
    { label: '成功', value: succeeded, icon: CheckCircle2, tone: 'text-emerald-500' },
    { label: '失败', value: failed, icon: AlertTriangle, tone: 'text-red-500' },
    { label: '进行中', value: running, icon: Clock3, tone: 'text-amber-500' },
  ]

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-[var(--text-primary)]">任务记录</h1>
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </Button>
      </div>

      {/* Metrics */}
      <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
        {metricCards.map(({ label, value, icon: Icon, tone }) => (
          <div
            key={label}
            className="flex items-center gap-3 rounded-xl border border-[var(--border)] bg-[var(--bg-card)] px-4 py-3"
          >
            <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--chip-bg)] ${tone}`}>
              <Icon className="h-4 w-4" />
            </div>
            <div>
              <div className="text-[11px] text-[var(--text-muted)] uppercase tracking-wider">{label}</div>
              <div className="text-lg font-semibold text-[var(--text-primary)]">{value}</div>
            </div>
          </div>
        ))}
      </div>

      {/* Filters — inline with table header */}
      <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] overflow-hidden">
        <div className="flex items-center gap-3 border-b border-[var(--border)] px-4 py-2.5">
          <span className="text-sm font-medium text-[var(--text-primary)]">最近任务</span>
          <div className="flex-1" />
          <div className="flex items-center gap-2">
            <div className="relative">
              <select
                value={platform}
                onChange={(e) => setPlatform(e.target.value)}
                className="h-8 appearance-none rounded-md border border-[var(--border)] bg-[var(--bg-input)] pl-3 pr-7 text-xs text-[var(--text-secondary)] transition-colors hover:border-[var(--accent)] focus:border-[var(--accent)]"
              >
                <option value="">全部平台</option>
                {platforms.map((item: any) => (
                  <option key={item.name} value={item.name}>{item.display_name}</option>
                ))}
              </select>
              <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3 w-3 -translate-y-1/2 text-[var(--text-muted)]" />
            </div>
            <div className="relative">
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
                className="h-8 appearance-none rounded-md border border-[var(--border)] bg-[var(--bg-input)] pl-3 pr-7 text-xs text-[var(--text-secondary)] transition-colors hover:border-[var(--accent)] focus:border-[var(--accent)]"
              >
                <option value="">全部状态</option>
                <option value="running">运行中</option>
                <option value="succeeded">成功</option>
                <option value="failed">失败</option>
                <option value="cancelled">已取消</option>
                <option value="interrupted">已中断</option>
              </select>
              <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3 w-3 -translate-y-1/2 text-[var(--text-muted)]" />
            </div>
            {(platform || status) && (
              <button
                onClick={() => { setPlatform(''); setStatus('') }}
                className="text-xs text-[var(--text-muted)] hover:text-[var(--accent)]"
              >
                清除
              </button>
            )}
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] bg-[var(--bg-pane)]">
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--text-muted)]">时间</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--text-muted)]">任务 ID</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--text-muted)]">平台</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--text-muted)]">状态</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--text-muted)]">进度</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--text-muted)]">成功/失败</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--text-muted)]">错误</th>
                <th className="px-4 py-2.5 text-right text-xs font-medium text-[var(--text-muted)]">操作</th>
              </tr>
            </thead>
            <tbody>
              {tasks.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-4 py-12 text-center text-sm text-[var(--text-muted)]">
                    暂无任务记录
                  </td>
                </tr>
              )}
              {tasks.map((task) => {
                const success = task.success || 0
                const errorCount = task.error_count || 0
                const total = success + errorCount
                const errorText = formatError(task.error)
                return (
                  <tr
                    key={task.id}
                    className="border-b border-[var(--border)]/50 transition-colors hover:bg-[var(--bg-hover)]"
                  >
                    <td className="whitespace-nowrap px-4 py-3 text-xs text-[var(--text-muted)]">
                      {task.created_at
                        ? new Date(task.created_at).toLocaleString('zh-CN', {
                            month: '2-digit',
                            day: '2-digit',
                            hour: '2-digit',
                            minute: '2-digit',
                            hour12: false,
                          })
                        : '-'}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className="cursor-default font-mono text-xs text-[var(--text-muted)]"
                        title={task.id}
                      >
                        {shortId(task.id)}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant="secondary">{task.platform || '-'}</Badge>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant={TASK_STATUS_VARIANTS[task.status] || 'secondary'}>
                        {getTaskStatusText(task.status)}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-xs text-[var(--text-secondary)]">
                      {task.progress || '-'}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        {total > 0 ? (
                          <>
                            <div className="flex h-1.5 w-16 overflow-hidden rounded-full bg-[var(--chip-bg)]">
                              {success > 0 && (
                                <div
                                  className="h-full bg-emerald-500 rounded-full"
                                  style={{ width: `${(success / total) * 100}%` }}
                                />
                              )}
                              {errorCount > 0 && (
                                <div
                                  className="h-full bg-red-500 rounded-full"
                                  style={{ width: `${(errorCount / total) * 100}%` }}
                                />
                              )}
                            </div>
                            <span className="text-xs text-[var(--text-muted)] whitespace-nowrap">
                              <span className="text-emerald-500">{success}</span>
                              {' / '}
                              <span className="text-red-500">{errorCount}</span>
                            </span>
                          </>
                        ) : (
                          <span className="text-xs text-[var(--text-muted)]">-</span>
                        )}
                      </div>
                    </td>
                    <td className="max-w-[280px] px-4 py-3">
                      {errorText ? (
                        <span
                          className="block truncate text-xs text-red-500 cursor-default"
                          title={task.error || ''}
                        >
                          {errorText}
                        </span>
                      ) : (
                        <span className="text-xs text-[var(--text-muted)]">-</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => openDetail(task)}
                          className="h-7 px-2 text-xs"
                        >
                          <Eye className="mr-1 h-3 w-3" />
                          详情
                        </Button>
                        {task.cancellable && (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => terminateTask(task)}
                            disabled={cancellingId === task.id}
                            className="h-7 border-red-500/40 px-2 text-xs text-red-400 hover:bg-red-500/10 hover:text-red-300"
                          >
                            <Square className="mr-1 h-3 w-3" />
                            {cancellingId === task.id ? '终止中' : '终止'}
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {selectedTask && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-4">
          <div className="flex max-h-[90vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--bg-card)] shadow-2xl">
            <div className="flex items-start justify-between gap-4 border-b border-[var(--border)] px-5 py-4">
              <div>
                <div className="flex items-center gap-2">
                  <h2 className="text-base font-semibold text-[var(--text-primary)]">任务详情</h2>
                  <Badge variant={TASK_STATUS_VARIANTS[selectedTask.status] || 'secondary'}>
                    {getTaskStatusText(selectedTask.status)}
                  </Badge>
                </div>
                <div className="mt-1 font-mono text-xs text-[var(--text-muted)]">{selectedTask.id}</div>
              </div>
              <div className="flex items-center gap-2">
                {selectedTask.cancellable && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => terminateTask(selectedTask)}
                    disabled={cancellingId === selectedTask.id}
                    className="border-red-500/40 text-red-400 hover:bg-red-500/10 hover:text-red-300"
                  >
                    <Square className="mr-1 h-3.5 w-3.5" />
                    立即终止
                  </Button>
                )}
                <button
                  onClick={() => setSelectedTask(null)}
                  className="rounded-full p-2 text-[var(--text-muted)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>
            <div className="grid min-h-0 flex-1 gap-4 overflow-y-auto p-5 lg:grid-cols-[320px_minmax(0,1fr)]">
              <div className="space-y-3">
                <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-pane)] p-4">
                  <div className="mb-3 text-sm font-medium text-[var(--text-primary)]">基础信息</div>
                  <div className="space-y-2 text-xs">
                    {[
                      ['平台', selectedTask.platform || '-'],
                      ['类型', selectedTask.type || '-'],
                      ['进度', selectedTask.progress || '-'],
                      ['成功', selectedTask.success ?? 0],
                      ['失败', selectedTask.error_count ?? 0],
                      ['创建', selectedTask.created_at ? new Date(selectedTask.created_at).toLocaleString('zh-CN') : '-'],
                      ['开始', selectedTask.started_at ? new Date(selectedTask.started_at).toLocaleString('zh-CN') : '-'],
                      ['结束', selectedTask.finished_at ? new Date(selectedTask.finished_at).toLocaleString('zh-CN') : '-'],
                    ].map(([label, value]) => (
                      <div key={String(label)} className="flex justify-between gap-3">
                        <span className="text-[var(--text-muted)]">{label}</span>
                        <span className="text-right text-[var(--text-secondary)]">{String(value)}</span>
                      </div>
                    ))}
                  </div>
                </div>
                {selectedTask.error && (
                  <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-4">
                    <div className="mb-2 text-sm font-medium text-red-300">错误</div>
                    <div className="break-words text-xs text-red-200/90">{selectedTask.error}</div>
                  </div>
                )}
                <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-pane)] p-4">
                  <div className="mb-2 flex items-center justify-between">
                    <div className="text-sm font-medium text-[var(--text-primary)]">参数 / 结果</div>
                    {detailLoading && <span className="text-xs text-[var(--text-muted)]">刷新中...</span>}
                  </div>
                  <pre className="max-h-[360px] overflow-auto rounded-lg bg-black/20 p-3 text-[11px] leading-5 text-[var(--text-secondary)]">
                    {JSON.stringify({ payload: selectedTask.payload, result: selectedTask.result }, null, 2)}
                  </pre>
                </div>
              </div>
              <div className="min-h-[520px]">
                <TaskLogPanel
                  taskId={selectedTask.id}
                  onDone={async () => {
                    try {
                      const latest = await apiFetch(`/tasks/${selectedTask.id}`)
                      setSelectedTask(latest)
                      await load()
                    } catch {
                      // ignore
                    }
                  }}
                />
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
