import { AskView } from "@/components/AskView";
import { Masthead } from "@/components/Masthead";

export default function Home() {
  return (
    <>
      <Masthead signedIn />
      <AskView />
    </>
  );
}
