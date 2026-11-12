import { Masthead } from "@/components/Masthead";
import { PlansView } from "@/components/PlansView";

export const metadata = { title: "Plans and indexes · Schemalab" };

export default function PlansPage() {
  return (
    <>
      <Masthead signedIn current="plans" />
      <PlansView />
    </>
  );
}
