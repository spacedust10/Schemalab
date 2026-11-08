const API_URL = process.env.API_URL ?? "http://localhost:8000";

async function apiStatus(): Promise<string> {
  try {
    const res = await fetch(`${API_URL}/health`, { cache: "no-store" });
    const body = (await res.json()) as { status: string; version: string };
    return `API ${body.status}, v${body.version}`;
  } catch {
    return "API unreachable";
  }
}

export default async function Home() {
  return (
    <main style={{ maxWidth: "40rem", margin: "4rem auto", padding: "0 1rem" }}>
      <h1>Schemalab</h1>
      <p style={{ color: "var(--muted)" }}>{await apiStatus()}</p>
    </main>
  );
}
