import { Masthead } from "@/components/Masthead";
import { NormalizeView } from "@/components/NormalizeView";

export const metadata = { title: "Normalize · Schemalab" };

export default function NormalizePage() {
  return (
    <>
      <Masthead signedIn current="normalize" />
      <NormalizeView />
    </>
  );
}
