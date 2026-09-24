import { redirect } from "next/navigation";

/**
 * Voice lives in the chat now: a spoken conversation is a chat conversation
 * — same answers, sources and history — started from the chat's voice
 * button. Old links to the Voice tab open the chat with the voice bar ready.
 */
export default function VoiceRedirect() {
  redirect("/app/chat?voice=1");
}
