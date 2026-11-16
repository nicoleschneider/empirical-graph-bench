import { createContext, useContext } from "react";
import type { Hardware, Session } from "./api";

export const HardwareContext = createContext<Hardware[]>([]);

export function useHardware(): Hardware[] {
  return useContext(HardwareContext);
}

export function labelOf(hardware: Hardware[], id: string): string {
  return hardware.find((h) => h.id === id)?.label ?? id;
}

export function idOf(hardware: Hardware[], label: string): string {
  return hardware.find((h) => h.label === label)?.id ?? label;
}

export function slotFinder(session: Session | null | undefined, hardware: Hardware[]) {
  return (hardwareLabel: string, implementation: string, representation: string): number =>
    session?.config.candidates.find(
      (c) =>
        c.implementation === implementation &&
        c.representation === representation &&
        labelOf(hardware, c.hardware) === hardwareLabel,
    )?.color_slot ?? 1;
}
