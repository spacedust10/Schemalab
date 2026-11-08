import Link from "next/link";
import { SignOut } from "./SignOut";

export function Masthead({ signedIn = false }: { signedIn?: boolean }) {
  return (
    <header className="masthead">
      {/* No prefetch: on the sign-in page it would cache / as a redirect back to /login. */}
      <Link className="wordmark" href="/" prefetch={false}>
        Schemalab<small>database lab</small>
      </Link>
      {signedIn && <SignOut />}
    </header>
  );
}
