import type { ReactNode } from "react";

export function N({ children }: { children: ReactNode }) {
  return <span className="n">{children}</span>;
}
