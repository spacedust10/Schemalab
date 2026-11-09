import { DataView } from "@/components/DataView";
import { Masthead } from "@/components/Masthead";

export const metadata = { title: "Your data · Schemalab" };

export default function DataPage() {
  return (
    <>
      <Masthead signedIn current="data" />
      <DataView />
    </>
  );
}
