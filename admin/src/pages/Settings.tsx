import { useEffect, useState } from 'react'
import { Plus, Save, X } from 'lucide-react'
import { useSettings, useUpdateSettings } from '../hooks/queries'
import type { AlertThresholds, DigestConfig } from '../lib/api'

function NumberField({
  label, value, onChange,
}: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <label className="text-xs text-slate-600">
      {label}
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-0.5 w-full rounded border border-slate-200 px-2 py-1 text-sm"
      />
    </label>
  )
}

function Toggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!on)}
      className={'relative h-5 w-9 rounded-full transition ' + (on ? 'bg-brand-600' : 'bg-slate-300')}
    >
      <span className={'absolute top-0.5 h-4 w-4 rounded-full bg-white transition ' + (on ? 'left-4' : 'left-0.5')} />
    </button>
  )
}

export function Settings() {
  const { data, isLoading, error } = useSettings()
  const update = useUpdateSettings()

  const [thresholds, setThresholds] = useState<AlertThresholds | null>(null)
  const [digest, setDigest] = useState<DigestConfig | null>(null)
  const [recipients, setRecipients] = useState<string[]>([])
  const [newEmail, setNewEmail] = useState('')

  useEffect(() => {
    if (data) {
      setThresholds(data.alert_thresholds)
      setDigest(data.digest)
      setRecipients(data.recipients)
    }
  }, [data])

  if (isLoading) return <div className="text-sm text-slate-500">Loading…</div>
  if (error || !data || !thresholds || !digest) {
    return <div className="text-sm text-red-600">Failed to load settings.</div>
  }

  function addRecipient() {
    const email = newEmail.trim()
    if (email && !recipients.includes(email)) setRecipients([...recipients, email])
    setNewEmail('')
  }

  function save() {
    update.mutate({ alert_thresholds: thresholds!, digest: digest!, recipients })
  }

  return (
    <div className="max-w-2xl space-y-6">
      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <h3 className="mb-3 text-base font-semibold text-slate-900">Alert thresholds</h3>
        <div className="grid grid-cols-2 gap-3">
          <NumberField
            label="Signup drop %"
            value={thresholds.signup_drop_pct}
            onChange={(v) => setThresholds({ ...thresholds, signup_drop_pct: v })}
          />
          <NumberField
            label="Signup window (days)"
            value={thresholds.signup_window_days}
            onChange={(v) => setThresholds({ ...thresholds, signup_window_days: v })}
          />
          <NumberField
            label="Signup min baseline"
            value={thresholds.signup_min_baseline}
            onChange={(v) => setThresholds({ ...thresholds, signup_min_baseline: v })}
          />
          <NumberField
            label="Error rate % (Phase 5)"
            value={thresholds.error_rate_pct}
            onChange={(v) => setThresholds({ ...thresholds, error_rate_pct: v })}
          />
          <NumberField
            label="Alert cooldown (hours)"
            value={thresholds.alert_cooldown_hours}
            onChange={(v) => setThresholds({ ...thresholds, alert_cooldown_hours: v })}
          />
          <div className="flex items-center justify-between text-xs text-slate-600">
            <span>Over seat-limit alerts</span>
            <Toggle
              on={thresholds.over_seat_limit_enabled}
              onChange={(v) => setThresholds({ ...thresholds, over_seat_limit_enabled: v })}
            />
          </div>
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <h3 className="mb-3 text-base font-semibold text-slate-900">Digest</h3>
        <div className="flex items-center gap-6 text-sm">
          <label className="flex items-center gap-2">
            <span className="text-slate-600">Enabled</span>
            <Toggle on={digest.enabled} onChange={(v) => setDigest({ ...digest, enabled: v })} />
          </label>
          <label className="flex items-center gap-2">
            <span className="text-slate-600">Frequency</span>
            <select
              value={digest.frequency}
              onChange={(e) => setDigest({ ...digest, frequency: e.target.value as 'daily' | 'weekly' })}
              className="rounded border border-slate-200 px-2 py-1 text-sm"
            >
              <option value="daily">Daily</option>
              <option value="weekly">Weekly</option>
            </select>
          </label>
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <h3 className="mb-3 text-base font-semibold text-slate-900">Recipients</h3>
        <div className="mb-3 space-y-1.5">
          {recipients.length === 0 && <p className="text-xs text-slate-400">No recipients yet.</p>}
          {recipients.map((email) => (
            <div key={email} className="flex items-center justify-between rounded-lg border border-slate-100 px-3 py-1.5">
              <span className="text-sm text-slate-700">{email}</span>
              <button
                onClick={() => setRecipients(recipients.filter((e) => e !== email))}
                className="text-slate-400 hover:text-red-600"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
        <div className="flex gap-2">
          <input
            type="email"
            value={newEmail}
            onChange={(e) => setNewEmail(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && addRecipient()}
            placeholder="ops@forgesop.app"
            className="flex-1 rounded-lg border border-slate-200 px-3 py-1.5 text-sm"
          />
          <button
            onClick={addRecipient}
            className="flex items-center gap-1 rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
          >
            <Plus className="h-4 w-4" /> Add
          </button>
        </div>
      </section>

      <button
        onClick={save}
        disabled={update.isPending}
        className="flex items-center gap-1 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-40"
      >
        <Save className="h-4 w-4" />
        {update.isPending ? 'Saving…' : 'Save settings'}
      </button>
      {update.isError && <p className="text-sm text-red-600">Failed to save.</p>}
      {update.isSuccess && <p className="text-sm text-green-600">Saved.</p>}
    </div>
  )
}
