/**
 * The site's Content-Security-Policy. Everything comes from this site: scripts, styles,
 * fonts (next/font serves them locally), workers and every request a page makes, so the
 * in-browser DuckDB cannot reach another site even if its own lock were lifted.
 * Next.js puts inline scripts in each page; they run because they carry this request's
 * nonce, so an injected inline script without it does not. 'wasm-unsafe-eval' lets
 * WebAssembly compile without allowing eval(). Development also needs eval for hot reload.
 */
export function contentSecurityPolicy(development: boolean, nonce: string): string {
  const scripts = ["'self'", `'nonce-${nonce}'`, "'wasm-unsafe-eval'", ...(development ? ["'unsafe-eval'"] : [])];
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
