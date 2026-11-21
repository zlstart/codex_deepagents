import { Sparkle } from "lucide-react";

export function Brand() {
  return (
    <div className="brand" role="img" aria-label="FAMSUN 丰尚">
      <img src="/images/famsun-brand.svg" alt="" width="128" height="22" />
      <span aria-hidden="true">丰尚</span>
    </div>
  );
}

export function AgentMark({ large = false }: { large?: boolean }) {
  return (
    <span className={"agent-mark" + (large ? " large" : "")} aria-hidden="true">
      <Sparkle size={large ? 30 : 17} strokeWidth={1.8} />
    </span>
  );
}
