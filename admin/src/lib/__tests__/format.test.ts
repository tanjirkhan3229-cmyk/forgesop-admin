import { describe, expect, it } from 'vitest'
import { formatBytes, formatDate, formatNumber, relativeTime } from '../format'

describe('formatNumber', () => {
  it('groups thousands', () => {
    expect(formatNumber(1234567)).toBe('1,234,567')
    expect(formatNumber(0)).toBe('0')
  })
})

describe('relativeTime', () => {
  const now = new Date('2026-06-14T12:00:00Z')
  it('returns a dash for null', () => {
    expect(relativeTime(null, now)).toBe('—')
  })
  it('buckets recent times', () => {
    expect(relativeTime('2026-06-14T11:59:30Z', now)).toBe('just now')
    expect(relativeTime('2026-06-14T11:30:00Z', now)).toBe('30m ago')
    expect(relativeTime('2026-06-14T09:00:00Z', now)).toBe('3h ago')
    expect(relativeTime('2026-06-11T12:00:00Z', now)).toBe('3d ago')
  })
})

describe('formatDate', () => {
  it('handles null and bad input', () => {
    expect(formatDate(null)).toBe('—')
    expect(formatDate('not-a-date')).toBe('—')
  })
})

describe('formatBytes', () => {
  it('scales to human units', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(1024 * 1024)).toBe('1 MB')
    expect(formatBytes(1024 ** 3)).toBe('1 GB')
  })
})
