import { Masthead } from "@/components/Masthead";
import { TakeView } from "@/components/TakeView";

export const metadata = { title: "Assignment · Schemalab" };

export default async function TakePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <>
      <Masthead signedIn current="assignments" />
      <TakeView id={decodeURIComponent(id)} />
    </>
  );
}
