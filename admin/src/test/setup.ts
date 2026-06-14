// Vitest setup. jsdom provides sessionStorage; individual tests stub `fetch`.
import { afterEach, vi } from 'vitest'

afterEach(() => {
  vi.restoreAllMocks()
  sessionStorage.clear()
})
