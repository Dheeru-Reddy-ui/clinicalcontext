/**
 * Opening the floating health assistant from elsewhere on the page — the
 * landing page's "Ask a health question" button — without either side
 * importing the other: the button dispatches, the launcher listens.
 */
export const OPEN_ASSISTANT_EVENT = "clinicalcontext:open-assistant";

export function openAssistant(): void {
  window.dispatchEvent(new Event(OPEN_ASSISTANT_EVENT));
}
