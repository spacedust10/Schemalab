import { Masthead } from "@/components/Masthead";
import { TheoryView } from "@/components/TheoryView";

export const metadata = { title: "Schedules and recovery · Schemalab" };

export default function TheoryPage() {
  return (
    <>
      <Masthead signedIn current="theory" />
      <TheoryView />
    </>
  );
}
