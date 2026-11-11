import Link from "next/link";
import { SignOut } from "./SignOut";

type Page = "ask" | "data" | "dashboard" | "normalize";

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
          </nav>
          <SignOut />
        </div>
      )}
    </header>
  );
}
