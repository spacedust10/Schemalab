import { LakehouseView } from "@/components/LakehouseView";
import { Masthead } from "@/components/Masthead";

export const metadata = { title: "Lakehouse · Schemalab" };

export default function LakehousePage() {
  return (
    <>
      <Masthead signedIn current="lakehouse" />
      <LakehouseView />
    </>
  );
}
