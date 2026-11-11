import { ERView } from "@/components/ERView";
import { Masthead } from "@/components/Masthead";

export const metadata = { title: "Design · Schemalab" };

export default function ERPage() {
  return (
    <>
      <Masthead signedIn current="er" />
      <ERView />
    </>
  );
}
