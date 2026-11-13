import { Masthead } from "@/components/Masthead";
import { PatternsView } from "@/components/PatternsView";

export const metadata = { title: "Transaction patterns · Schemalab" };

export default function PatternsPage() {
  return (
    <>
      <Masthead signedIn current="patterns" />
      <PatternsView />
    </>
  );
}
