/** Incremental parser for a text/event-stream body: feed chunks, get one callback per event. */
export function createSSEParser(onEvent: (event: unknown) => void): (chunk: string) => void {
  let buffer = "";
  return (chunk) => {
    buffer += chunk;
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? ""; // keep a trailing partial line for the next chunk
    for (const line of lines) {
      if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)));
    }
  };
}
