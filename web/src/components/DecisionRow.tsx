import type { ReactNode } from "react";
import { Card } from "@/components/ui";

// One row of a "pick the right category" queue (Review, Gold): title and badge, one line of facts, then the
// picker and its buttons on their own line, so every row of a list lines up whatever the length of its text.
export function DecisionRow({ title, badge, meta, picker, actions, children }: {
  title: ReactNode; badge?: ReactNode; meta: ReactNode; picker: ReactNode; actions: ReactNode; children?: ReactNode;
}) {
  return (
    <Card>
      <div className="flex min-w-0 flex-wrap items-center gap-2">{title}{badge}</div>
      <p className="mt-1 text-[13px] text-muted">{meta}</p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <div className="w-full sm:w-72">{picker}</div>
        {actions}
      </div>
      {children}
    </Card>
  );
}
