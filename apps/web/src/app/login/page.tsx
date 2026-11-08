import { Masthead } from "@/components/Masthead";
import { SignInForm } from "@/components/SignInForm";

export const metadata = { title: "Sign in · Schemalab" };

export default function LoginPage() {
  return (
    <>
      <Masthead />
      <main id="main" className="signin">
        <h1>Ask your data a question.</h1>
        <p className="lede">
          Plain English in, guarded SQL out. Every query is checked before it runs, and the
          model never sees your rows.
        </p>
        <SignInForm />
      </main>
    </>
  );
}
