import { LabView } from "@/components/LabView";
import { Masthead } from "@/components/Masthead";

export const metadata = { title: "Transaction lab · Schemalab" };

export default function LabPage() {
  return (
    <>
      <Masthead signedIn current="lab" />
      <LabView />
    </>
  );
}
