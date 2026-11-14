/**
 * The site's Content-Security-Policy. Everything comes from this site: scripts, styles,
 * fonts (next/font serves them locally), workers and every request a page makes, so the
 * in-browser DuckDB cannot reach another site even if its own lock were lifted.
 * 'wasm-unsafe-eval' lets WebAssembly compile without allowing eval(); Next.js puts inline
 * scripts in each page, so 'unsafe-inline' stays until pages carry nonces (which would make
 * every page dynamic). Development also needs eval for hot reload.
 */
export function contentSecurityPolicy(development: boolean): string {
  const scripts = ["'self'", "'unsafe-inline'", "'wasm-unsafe-eval'", ...(development ? ["'unsafe-eval'"] : [])];
  return [
    "default-src 'self'",
    `script-src ${scripts.join(" ")}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    "connect-src 'self'",
    "worker-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}
