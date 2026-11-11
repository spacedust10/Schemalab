import { AlgebraView } from "@/components/AlgebraView";
import { Masthead } from "@/components/Masthead";

export const metadata = { title: "Algebra · Schemalab" };

export default function AlgebraPage() {
  return (
    <>
      <Masthead signedIn current="algebra" />
      <AlgebraView />
    </>
  );
}
