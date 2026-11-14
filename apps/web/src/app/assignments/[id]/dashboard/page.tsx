import { AssignmentDashboard } from "@/components/AssignmentDashboard";
import { Masthead } from "@/components/Masthead";

export const metadata = { title: "Assignment dashboard · Schemalab" };

export default async function DashboardPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <>
      <Masthead signedIn current="assignments" />
      <AssignmentDashboard id={decodeURIComponent(id)} />
    </>
  );
}
