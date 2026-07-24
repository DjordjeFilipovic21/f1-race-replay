const SHA256_HEX = /^[0-9a-f]{64}$/

export function assertSha256(value: string): string {
  if (!SHA256_HEX.test(value)) throw new Error('Replay-data digest must be a lowercase SHA-256 hex string')
  return value
}

export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const buffer = new ArrayBuffer(bytes.byteLength)
  new Uint8Array(buffer).set(bytes)
  const digest = await globalThis.crypto.subtle.digest('SHA-256', buffer)
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

export async function verifyDigest(bytes: Uint8Array, expected: string): Promise<void> {
  if (await sha256Hex(bytes) !== assertSha256(expected)) throw new Error('Replay-data digest does not match')
}
