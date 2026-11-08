"use client";

import { useRouter } from "next/navigation";
import { postJSON } from "@/lib/post";

export function SignOut() {
  const router = useRouter();
  return (
    <button
      type="button"
      className="button quiet"
      onClick={async () => {
        await postJSON("/api/auth/logout", {});
        router.replace("/login");
        router.refresh(); // drop cached signed-in pages
      }}
    >
      Sign out
    </button>
  );
}
