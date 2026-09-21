"use client";

import type { ReactNode } from "react";

import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";

/** Bottom sheet that hosts the citation panel on narrow screens. */
export function MobileSourceSheet({ open, onClose, children }: { open: boolean; onClose: () => void; children: ReactNode }) {
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="bottom" className="h-[85vh] p-0">
        <SheetTitle className="sr-only">Source</SheetTitle>
        {children}
      </SheetContent>
    </Sheet>
  );
}
