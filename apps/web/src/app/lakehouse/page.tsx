import { Suspense } from "react";
import { LakehouseView } from "@/components/LakehouseView";
import { Masthead } from "@/components/Masthead";

export const metadata = { title: "Lakehouse · Schemalab" };

export default function LakehousePage() {
  return (
    <>
      <Masthead signedIn current="lakehouse" />
      {/* The view reads the open table from the URL's query string. */}
      <Suspense>
        <LakehouseView />
      </Suspense>
    </>
  );
}
