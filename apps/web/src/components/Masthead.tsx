import Link from "next/link";
import { SignOut } from "./SignOut";

type Page = "ask" | "data" | "dashboard" | "normalize" | "er" | "algebra" | "lab" | "theory" | "plans" | "bench";

export function Masthead({ signedIn = false, current }: { signedIn?: boolean; current?: Page }) {
  return (
    <header className="masthead">
      {/* No prefetch: on the sign-in page it would cache / as a redirect back to /login. */}
      <Link className="wordmark" href="/" prefetch={false}>
        Schemalab<small>database lab</small>
      </Link>
      {signedIn && (
        <div className="masthead-end">
          <nav aria-label="Main" className="nav">
            <Link href="/" prefetch={false} aria-current={current === "ask" ? "page" : undefined}>
              Ask
            </Link>
            <Link href="/data" prefetch={false} aria-current={current === "data" ? "page" : undefined}>
              Your data
            </Link>
            <Link
              href="/dashboard"
              prefetch={false}
              aria-current={current === "dashboard" ? "page" : undefined}
            >
              Dashboard
            </Link>
            <Link
              href="/normalize"
              prefetch={false}
              aria-current={current === "normalize" ? "page" : undefined}
            >
              Normalize
            </Link>
            <Link href="/er" prefetch={false} aria-current={current === "er" ? "page" : undefined}>
              Design
            </Link>
            <Link
              href="/algebra"
              prefetch={false}
              aria-current={current === "algebra" ? "page" : undefined}
            >
              Algebra
            </Link>
            <Link href="/lab" prefetch={false} aria-current={current === "lab" ? "page" : undefined}>
              Lab
            </Link>
            <Link href="/theory" prefetch={false} aria-current={current === "theory" ? "page" : undefined}>
              Theory
            </Link>
            <Link href="/plans" prefetch={false} aria-current={current === "plans" ? "page" : undefined}>
              Plans
            </Link>
            <Link href="/bench" prefetch={false} aria-current={current === "bench" ? "page" : undefined}>
              Bench
            </Link>
          </nav>
          <SignOut />
        </div>
      )}
    </header>
  );
}
