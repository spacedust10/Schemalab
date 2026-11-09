import { DashboardView } from "@/components/DashboardView";
import { Masthead } from "@/components/Masthead";

export const metadata = { title: "Dashboard · Schemalab" };

export default function DashboardPage() {
  return (
    <>
      <Masthead signedIn current="dashboard" />
      <DashboardView />
    </>
  );
}
