"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { errorText, postJSON } from "@/lib/post";

type Mode = "login" | "register";

export function SignInForm() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("login");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(form: FormData) {
    setBusy(true);
    setError(null);
    const res = await postJSON(`/api/auth/${mode}`, {
      email: form.get("email"),
      password: form.get("password"),
    });
    setBusy(false);
    if (res.ok) {
      // The session changed, so fetch / fresh instead of from the signed-out router cache.
      router.replace("/");
      router.refresh();
      return;
    }
    if (res.status === 422) {
      setError(
        mode === "register"
          ? "Use a valid email and a password of at least 12 characters."
          : "Enter your email and password.",
      );
    } else {
      setError(await errorText(res, "Something went wrong. Try again."));
    }
  }

  const registering = mode === "register";
  return (
    <form action={submit} aria-describedby={error ? "signin-error" : undefined}>
      <div className="field">
        <label htmlFor="email">Email</label>
        <input id="email" name="email" type="email" autoComplete="email" required />
      </div>
      <div className="field">
        <label htmlFor="password">Password</label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete={registering ? "new-password" : "current-password"}
          minLength={registering ? 12 : undefined}
          required
        />
        {registering && <p className="hint">At least 12 characters. A short phrase works well.</p>}
      </div>
      {error && (
        <p id="signin-error" className="alert" role="alert">
          <strong>Error</strong>
          {error}
        </p>
      )}
      <button className="button" type="submit" disabled={busy}>
        {busy ? "Working…" : registering ? "Create account" : "Sign in"}
      </button>
      <button
        type="button"
        className="switch"
        onClick={() => {
          setMode(registering ? "login" : "register");
          setError(null);
        }}
      >
        {registering ? "I already have an account" : "New here? Create an account"}
      </button>
    </form>
  );
}
