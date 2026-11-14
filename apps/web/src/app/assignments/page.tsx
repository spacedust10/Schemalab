import { AssignmentsView } from "@/components/AssignmentsView";
import { Masthead } from "@/components/Masthead";

export const metadata = { title: "Assignments · Schemalab" };

export default function AssignmentsPage() {
  return (
    <>
      <Masthead signedIn current="assignments" />
      <AssignmentsView />
    </>
  );
}
