import { Masthead } from "@/components/Masthead";
import { PatternsView } from "@/components/PatternsView";

export const metadata = { title: "Data pipelines · Schemalab" };

export default function PipelinesPage() {
  return (
    <>
      <Masthead signedIn current="pipelines" />
      <PatternsView
        group="pipelines"
        title="Data pipelines"
        lede="Moving data from the application's tables into analytics, done for real in your lab: change data capture, partitioning, slowly changing dimensions and a star schema. Each runs the naive way beside the right one, with checks on what the numbers say."
      />
    </>
  );
}
