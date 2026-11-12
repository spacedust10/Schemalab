import { BenchView } from "@/components/BenchView";
import { Masthead } from "@/components/Masthead";

export const metadata = { title: "Benchmarks · Schemalab" };

export default function BenchPage() {
  return (
    <>
      <Masthead signedIn current="bench" />
      <BenchView />
    </>
  );
}
